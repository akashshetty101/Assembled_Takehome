import json
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.episodes import Transition
from app.domain.subjects import AgentState
from app.engine.engine import EngineDeps, evaluate_rule, on_event, on_tick
from app.evaluation.registry import build_registry
from app.ingest.counters import Counters
from app.ingest.pipeline import ingest_event
from app.routing.console_channel import ConsoleChannel
from app.scheduling.scan import ScanScheduler
from app.storage.repositories import RuleRecord
from app.storage.sqlite.counters import SqliteCountersRepository
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.events import SqliteEventsRepository
from app.storage.sqlite.notifications import SqliteNotificationsRepository
from app.storage.sqlite.rules import SqliteRulesRepository
from app.storage.sqlite.subject_state import SqliteAgentStateRepository, SqliteQueueStateRepository

REGISTRY = build_registry()


class _SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def _deps(db_conn, channels=None) -> EngineDeps:
    return EngineDeps(
        conn=db_conn,
        rules_repo=SqliteRulesRepository(db_conn),
        episodes_repo=SqliteEpisodesRepository(db_conn),
        queue_repo=SqliteQueueStateRepository(db_conn),
        agent_repo=SqliteAgentStateRepository(db_conn),
        registry=REGISTRY,
        channels=channels if channels is not None else [ConsoleChannel()],
        notifications_repo=SqliteNotificationsRepository(db_conn),
    )


ON_CALL_45MIN_RULE = RuleRecord(
    id="r45", name="agent on a single call > 45 min", subject_type="agent",
    selector_json='{"kind":"all"}',
    conditions_json=json.dumps([
        {"fact": "current_state", "op": "eq", "value": "on_call"},
        {"fact": "current_state_duration_sec", "op": "gt", "value": 2700},
    ]),
    for_duration_sec=0, cooldown_sec=0, recipient_kind="author", recipient_target=None,
    template="{subject_id} has been on a single call for {current_state_duration_sec}s",
    enabled=True, created_by="lead_sam", created_at="2026-05-26T09:00:00Z", version=1,
)


def test_evaluate_rule_opens_and_dispatches_when_matched(db_conn):
    SqliteRulesRepository(db_conn).create(ON_CALL_45MIN_RULE)
    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_11", current_state="on_call",
                   state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=now)
    )
    spy = _SpyChannel()
    transitions = evaluate_rule("r45", "a_11", now, "event", _deps(db_conn, channels=[spy]))
    assert transitions == [Transition.OPENED]
    assert len(spy.sent) == 1
    assert "a_11" in spy.sent[0].body


def test_evaluate_rule_disabled_rule_returns_nothing(db_conn):
    import dataclasses

    disabled = dataclasses.replace(ON_CALL_45MIN_RULE, enabled=False)
    SqliteRulesRepository(db_conn).create(disabled)
    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_11", current_state="on_call",
                   state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=now)
    )
    transitions = evaluate_rule("r45", "a_11", now, "event", _deps(db_conn))
    assert transitions == []


def test_evaluate_rule_missing_subject_state_returns_nothing(db_conn):
    SqliteRulesRepository(db_conn).create(ON_CALL_45MIN_RULE)
    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    transitions = evaluate_rule("r45", "a_no_such_agent", now, "event", _deps(db_conn))
    assert transitions == []


def test_trigger_is_a_label_only_and_does_not_branch_behavior(db_conn):
    """Same function, two triggers: 'event' and 'due' from identical state
    -> byte-identical result and transitions."""
    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    state = AgentState(agent_id="a_11", current_state="on_call",
                        state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=now)

    SqliteRulesRepository(db_conn).create(ON_CALL_45MIN_RULE)
    SqliteAgentStateRepository(db_conn).put(state)
    result_event = evaluate_rule("r45", "a_11", now, "event", _deps(db_conn))

    # Fresh DB, identical setup, only the trigger label differs.
    import tempfile

    from app.config import Settings
    from app.storage.db import connect, migrate

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(db_path=f"{tmp}/t.db", schema_path="app/storage/schema.sql")
        conn2 = connect(settings)
        migrate(conn2, settings)
        SqliteRulesRepository(conn2).create(ON_CALL_45MIN_RULE)
        SqliteAgentStateRepository(conn2).put(state)
        result_due = evaluate_rule("r45", "a_11", now, "due", _deps(conn2))
        conn2.close()

    assert result_event == result_due == [Transition.OPENED]


def test_evaluate_rule_counts_evaluations_by_trigger_without_it_affecting_outcome(db_conn):
    """PLAN.md Phase 5 item 7: 'evaluation counts by trigger (event vs due)'.
    `trigger` remains a stats label only -- it drives which counter is
    incremented, never a branch in evaluation logic (see the trigger-purity
    test above)."""
    import dataclasses

    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    SqliteRulesRepository(db_conn).create(ON_CALL_45MIN_RULE)
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_11", current_state="on_call",
                   state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=now)
    )
    counters = Counters(SqliteCountersRepository(db_conn))
    deps = dataclasses.replace(_deps(db_conn), counters=counters)

    evaluate_rule("r45", "a_11", now, "event", deps)
    evaluate_rule("r45", "a_11", now, "due", deps)
    evaluate_rule("r45", "a_11", now, "due", deps)

    assert counters.get("evaluations_event") == 1
    assert counters.get("evaluations_due") == 2


def test_evaluate_rule_counter_increment_survives_early_return_and_reconnect(db_conn, tmp_path):
    """Code-review HIGH: the counter increment must be committed even when
    evaluate_rule returns early (no rule / disabled / no subject state) --
    otherwise it rides along in an open, uncommitted transaction that is
    only durable by accident of some later unrelated write, exactly the bug
    class ingest/pipeline.py's per-increment transaction() already guards
    against. Reproduced against a real file DB: close and reopen the same
    connection to prove the count actually persisted, not just that it read
    back correctly on the same live connection."""
    from app.config import Settings
    from app.storage.db import connect

    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    import dataclasses

    counters = Counters(SqliteCountersRepository(db_conn))
    deps = dataclasses.replace(_deps(db_conn), counters=counters)

    # No such rule -> early return before the rule lookup even resolves.
    evaluate_rule("r_no_such_rule", "a_no_such_agent", now, "due", deps)

    db_path = db_conn.execute("PRAGMA database_list").fetchone()[2]
    db_conn.close()

    reopened = connect(Settings(db_path=db_path, schema_path="app/storage/schema.sql"))
    persisted = Counters(SqliteCountersRepository(reopened)).get("evaluations_due")
    reopened.close()

    assert persisted == 1


def test_evaluate_rule_does_not_resend_when_notification_row_already_exists(db_conn):
    """Phase 6 item 1: persist-then-send, gated on insert_if_absent's
    rowcount. A duplicate (episode_id, transition, occurrence_seq) --
    exactly R9's idempotency key -- must not reach Channel.send() a second
    time, even though advance() correctly recomputes the same transition
    (a crash-recovery replay can legitimately re-derive an already-persisted
    reminder). Forces the collision directly by pre-inserting the row
    advance() is about to compute, rather than relying on a specific crash
    scenario to reproduce it."""
    import dataclasses

    from app.storage.repositories import EpisodeRecord, NotificationRecord

    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)
    rule = dataclasses.replace(ON_CALL_45MIN_RULE, cooldown_sec=300)
    SqliteRulesRepository(db_conn).create(rule)
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_11", current_state="on_call",
                   state_entered_at=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc), last_event_ts=now)
    )
    episodes_repo = SqliteEpisodesRepository(db_conn)
    episodes_repo.create(EpisodeRecord(
        id="ep1", rule_id="r45", subject_id="a_11", state="open",
        first_true_at=None, opened_at="2026-05-26T09:00:00Z", closed_at=None,
        last_notified_at="2026-05-26T09:40:00Z", notify_seq=0,
        evaluations_suppressed=0, stale=False,
    ))
    notifications_repo = SqliteNotificationsRepository(db_conn)
    notifications_repo.insert_if_absent(NotificationRecord(
        id="already-there", episode_id="ep1", rule_id="r45", subject_id="a_11",
        transition="reminder", occurrence_seq=1, recipient_kind="author", recipient_target="lead_sam",
        body="pre-existing", facts_snapshot_json="{}",
        event_time="2026-05-26T09:55:01Z", created_at="2026-05-26T09:55:01Z",
    ))

    spy = _SpyChannel()
    transitions = evaluate_rule("r45", "a_11", now, "event", _deps(db_conn, channels=[spy]))

    assert transitions == [Transition.REMINDER]
    assert len(spy.sent) == 0
    assert len(notifications_repo.list()) == 1


def test_engine_always_reloads_subject_state_never_trusts_caller():
    """evaluate_rule takes no state parameter at all -- it is structurally
    impossible to pass stale state; every call re-reads from the repo."""
    import inspect

    sig = inspect.signature(evaluate_rule)
    assert "state" not in sig.parameters


# -- The a_11 headline test ---------------------------------------------------

A11_SETUP_EVENT = {
    "event_id": "evt_01HXYZ007", "ts": "2026-05-26T09:00:45Z", "type": "agent_state_change",
    "agent_id": "a_11", "queue_ids": ["tier_2", "vip"], "previous_state": None,
    "previous_state_duration_sec": None, "new_state": "available",
}
A11_ON_CALL_EVENT = {
    "event_id": "evt_01HXYZ018", "ts": "2026-05-26T09:10:00Z", "type": "agent_state_change",
    "agent_id": "a_11", "queue_ids": ["tier_2", "vip"], "previous_state": "available",
    "previous_state_duration_sec": 555, "new_state": "on_call",
}
A11_EXIT_EVENT = {
    "event_id": "evt_01HXYZ089", "ts": "2026-05-26T10:20:00Z", "type": "agent_state_change",
    "agent_id": "a_11", "queue_ids": ["tier_2", "vip"], "previous_state": "on_call",
    "previous_state_duration_sec": 4200, "new_state": "available",
}


def test_a11_headline_fires_in_the_0955_minute_not_1020(db_conn):
    """PLAN.md's headline test: ingest the file with the 45-min rule
    active, ticks driven by event time -> exactly one opened for a_11
    driven by TIME (not by the 10:20 exit event's previous_state_duration_sec).

    Exact-timestamp guard, not a "notification exists" one: fired_at must
    fall in the 09:55 minute (state_entered_at=09:10 + 2700s threshold),
    and current_state_duration_sec at that instant must be the smallest
    value strictly greater than 2700 that the tick grid can produce --
    proving R8's strict '>' boundary held (exactly 2700 at 09:55:00 does
    NOT fire) while still proving the fix is genuinely time-driven, not a
    25-minutes-late artifact of reading the exit event's
    previous_state_duration_sec=4200 (which would put it at 10:20 instead).
    Uses tick_interval_sec=5, matching config.py's default."""
    from app.clock import ManualClock

    SqliteRulesRepository(db_conn).create(ON_CALL_45MIN_RULE)
    deps = _deps(db_conn)
    ingest_repos = {
        "conn": db_conn, "events_repo": SqliteEventsRepository(db_conn),
        "counters": Counters(SqliteCountersRepository(db_conn)),
        "queue_repo": SqliteQueueStateRepository(db_conn), "agent_repo": SqliteAgentStateRepository(db_conn),
    }
    scheduler = ScanScheduler(deps.episodes_repo, deps.rules_repo, deps.queue_repo, deps.agent_repo)

    clock = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    seq = 1

    def ingest(raw):
        nonlocal seq
        clock.set(datetime.fromisoformat(raw["ts"].replace("Z", "+00:00")))
        outcome = ingest_event(raw, received_seq=seq, **ingest_repos)
        seq += 1
        if outcome.changed_subject is not None:
            on_event(outcome.changed_subject, clock.now(), deps)

    ingest(A11_SETUP_EVENT)  # 09:00:45
    ingest(A11_ON_CALL_EVENT)  # 09:10:00 -- on_call begins

    all_transitions_by_time: list[tuple[datetime, list]] = []
    tick_interval = timedelta(seconds=5)
    tick_time = datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc) + tick_interval
    end_time = datetime(2026, 5, 26, 10, 20, tzinfo=timezone.utc)
    while tick_time < end_time:
        clock.set(tick_time)
        transitions = on_tick(tick_time, deps, scheduler)
        if transitions:
            all_transitions_by_time.append((tick_time, transitions))
        tick_time += tick_interval

    ingest(A11_EXIT_EVENT)  # 10:20:00 -- leaves on_call, event-driven resolve

    opened_events = [(t, tr) for t, tr in all_transitions_by_time if Transition.OPENED in tr]
    assert len(opened_events) == 1
    fired_at, _ = opened_events[0]

    state_entered_at = datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc)
    duration_at_fire = (fired_at - state_entered_at).total_seconds()
    assert fired_at.hour == 9 and fired_at.minute == 55  # the 09:55 minute, not 10:20
    assert duration_at_fire > 2700  # R8: strict > -- exactly 2700 does not fire
    assert duration_at_fire <= 2700 + tick_interval.total_seconds()  # the very next tick, not later

    notifications = deps.notifications_repo.list()
    opened_notifications = [n for n in notifications if n.transition == "opened"]
    assert len(opened_notifications) == 1
    assert opened_notifications[0].event_time == fired_at.isoformat()
