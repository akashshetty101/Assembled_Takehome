"""
Event Replay CLI & Simulator.

This CLI tool is both the primary demonstration runner and the system's integration 
test harness. It replays a JSONL event stream through the ingestion pipeline and 
notification engine under exact event-time clock control.

Crucial Mechanics:
- Event-Time Clock: Uses a `ManualClock` injected into all dependencies. Time is driven 
  entirely by events, never system wall-clock time.
- Clock Interpolation (R11): For each event at time T, the clock is stepped forward in 
  `tick_interval_sec` increments from its current time up to T, calling `on_tick` at each 
  step, before finally setting the clock to T and ingesting the event. This prevents 
  skipping evaluation thresholds (e.g., agent a_11's 70-minute silent call) that have no 
  events during the breach window.
- Replay Watermark (Idempotency): Persists the clock's maximum progress in SQLite. A subsequent 
  replay run will start its clock from this watermark, preventing double-counting of 
  suppressed evaluations or re-triggering reminders.
- Replay Speed: Cosmetic sleeping between steps. A speed factor of 0 runs the simulation 
  as fast as possible.
"""

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.clock import ManualClock
from app.config import Settings
from app.engine.engine import EngineDeps, on_event, on_tick
from app.evaluation.registry import build_registry
from app.ingest.counters import Counters
from app.ingest.pipeline import ingest_event
from app.routing.console_channel import ConsoleChannel
from app.routing.inbox_channel import InboxChannel
from app.scheduling.scan import ScanScheduler
from app.storage.db import connect, migrate, transaction
from app.storage.sqlite.counters import SqliteCountersRepository
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.events import SqliteEventsRepository
from app.storage.sqlite.notifications import SqliteNotificationsRepository
from app.storage.sqlite.rules import SqliteRulesRepository
from app.storage.sqlite.subject_state import SqliteAgentStateRepository, SqliteQueueStateRepository


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _read_watermark(conn) -> datetime | None:
    """Phase 6 idempotency: how far a PRIOR replay() call into this same DB
    already ticked. Without this, a second call resets its ManualClock to
    the event file's first timestamp and re-walks ticks through time a
    prior call already fully evaluated -- generalizing the single-call R10
    guard ('ticks never move backward') across separate calls. An
    episode-derived proxy (e.g. last_notified_at) was tried first and
    rejected: it can't distinguish 'no further transitions occurred' from
    'ticks kept running and silently re-incrementing evaluations_suppressed
    after the last notification' -- only an explicit watermark can."""
    row = conn.execute("SELECT ticked_through FROM replay_watermark WHERE id = 1").fetchone()
    return _parse_ts(row["ticked_through"]) if row else None


def _write_watermark(conn, ts: datetime) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO replay_watermark (id, ticked_through) VALUES (1, :ts) "
            "ON CONFLICT(id) DO UPDATE SET ticked_through = :ts",
            {"ts": ts.isoformat()},
        )


def replay(events_path: Path, settings: Settings, *, speed: float | None = None, no_eval: bool = False) -> Counters:
    """The demo AND the test harness -- the clock design is the crux (R11).

    For each event at time T: first advance the clock in tick_interval_sec
    steps from clock.now() up to (but not past) T, calling on_tick at each
    step; THEN set the clock to exactly T and ingest. This interpolation is
    what makes a_11's 70-minute silent gap produce a tick that lands
    exactly on the 45-minute threshold crossing, instead of a single jump
    straight from 09:10 to 10:20 that would skip over 09:55 entirely and
    fire 25 minutes late reporting the wrong duration (R6/R11's "most
    dangerous bug" -- it would look almost right).

    Wall-clock speed is purely cosmetic (a time.sleep between steps) and
    never appears anywhere in what's computed as "now" for evaluation --
    only in how long the terminal pauses between ticks.
    """
    speed = speed if speed is not None else settings.replay_speed

    conn = connect(settings)
    migrate(conn, settings)

    events_repo = SqliteEventsRepository(conn)
    counters = Counters(SqliteCountersRepository(conn))
    queue_repo = SqliteQueueStateRepository(conn)
    agent_repo = SqliteAgentStateRepository(conn)
    rules_repo = SqliteRulesRepository(conn)
    episodes_repo = SqliteEpisodesRepository(conn)
    notifications_repo = SqliteNotificationsRepository(conn)
    registry = build_registry()

    deps = EngineDeps(
        conn=conn, rules_repo=rules_repo, episodes_repo=episodes_repo,
        queue_repo=queue_repo, agent_repo=agent_repo, registry=registry,
        channels=[ConsoleChannel(), InboxChannel()], notifications_repo=notifications_repo,
        counters=counters,
    )
    scheduler = ScanScheduler(episodes_repo, rules_repo, queue_repo, agent_repo)

    lines = [line for line in events_path.read_text().splitlines() if line.strip()]
    if not lines:
        conn.close()
        return counters

    tick_interval_sec = settings.tick_interval_sec
    file_first_ts = _parse_ts(json.loads(lines[0])["ts"])
    watermark = _read_watermark(conn)
    clock = ManualClock(max(file_first_ts, watermark) if watermark else file_first_ts)

    def step_to(target: datetime) -> None:
        while clock.now() + timedelta(seconds=tick_interval_sec) <= target:
            clock.advance(tick_interval_sec)
            if not no_eval:
                on_tick(clock.now(), deps, scheduler)
            if speed and speed > 0:
                time.sleep(tick_interval_sec / speed)

    def make_on_change():
        def on_change(ref, now):
            on_event(ref, now, deps)
        return on_change

    for seq, line in enumerate(lines, start=1):
        raw = json.loads(line)
        ts = _parse_ts(raw["ts"])
        # The known duplicate (line 95) and late event (line 96) in the real
        # sample file are chronologically BEFORE lines that already ran --
        # ingest/pipeline.py's own dedup/watermark logic correctly counts
        # them regardless of clock position, but stepping/setting the
        # ENGINE clock backward for them would corrupt every subsequent
        # tick's "now" (R10: ticks must be a pure function of event time,
        # not of file order). Only advance for genuine forward progress.
        if ts >= clock.now():
            step_to(ts)
            clock.set(ts)
        ingest_event(
            raw, received_seq=seq, conn=conn, events_repo=events_repo, counters=counters,
            queue_repo=queue_repo, agent_repo=agent_repo,
            on_change=None if no_eval else make_on_change(),
        )

    if not no_eval:
        # A no_eval pass still walks the clock forward (only the on_tick()
        # call itself is skipped) -- persisting that as "how far evaluation
        # progressed" would poison a later evaluation-enabled pass into the
        # same DB into silently skipping every tick/event (code-review
        # CRITICAL fix).
        _write_watermark(conn, clock.now())
    _print_counter_table(counters, queue_repo, agent_repo)
    conn.close()
    return counters


def _print_counter_table(counters: Counters, queue_repo, agent_repo) -> None:
    print("\n--- ingest counters ---")
    for name, value in counters.snapshot().items():
        print(f"{name:30s} {value}")
    print(f"{'subjects_tracked':30s} {queue_repo.count() + agent_repo.count()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a JSONL event stream through the notification engine.")
    parser.add_argument("events_path", type=Path, nargs="?", default=Path("Sample_Data/events.jsonl"))
    parser.add_argument("--no-eval", action="store_true", help="Ingest only; skip rule evaluation.")
    parser.add_argument("--speed", type=float, default=None, help="Wall-clock replay speed multiplier.")
    args = parser.parse_args()
    replay(args.events_path, Settings(), speed=args.speed, no_eval=args.no_eval)


if __name__ == "__main__":
    main()
