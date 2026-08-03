import json
from pathlib import Path

from app.config import Settings
from app.storage.db import connect

SAMPLE_DATA = Path(__file__).resolve().parent.parent / "Sample_Data" / "events.jsonl"


def _settings(tmp_path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "replay.db"),
        schema_path="app/storage/schema.sql",
        seeds_path="seeds/rules.json",
        tick_interval_sec=5,
        replay_speed=10000.0,
    )


def _seed_rules(conn, registry):
    """Test helper mirroring cli/seed.py's own logic. Repos never
    self-commit (Phase 0 convention) -- must wrap in transaction()."""
    import json as _json

    from app.domain.rules import Rule
    from app.storage.db import transaction
    from app.storage.repositories import RuleRecord
    from app.storage.sqlite.rules import SqliteRulesRepository

    raw_rules = _json.loads(Path("seeds/rules.json").read_text())
    repo = SqliteRulesRepository(conn)
    with transaction(conn):
        for i, raw in enumerate(raw_rules, start=1):
            rule = Rule.model_validate(raw, context={"registry": registry})
            repo.create(RuleRecord(
                id=f"r{i}", name=rule.name, subject_type=rule.subject_type.value,
                selector_json=rule.selector.model_dump_json(),
                conditions_json=json.dumps([c.model_dump(mode="json") for c in rule.conditions]),
                for_duration_sec=rule.for_duration_sec, cooldown_sec=rule.cooldown_sec,
                recipient_kind=rule.recipient.kind.value, recipient_target=rule.recipient.target,
                template=rule.template, enabled=rule.enabled, created_by=rule.created_by,
                created_at="2026-05-26T09:00:00Z", version=1,
            ))


def test_no_eval_ingests_the_file_and_produces_the_phase1_counters(tmp_path):
    """Phase 1's deferred 'Green at end' criterion, finally exercisable via
    the real CLI: replay --no-eval ingests the file and produces the exact
    ground-truth counters. replay() closes its own connection before
    returning, so counters are re-checked via a fresh connection/repo
    rather than the returned (now-unusable) Counters object."""
    from app.cli.replay import replay
    from app.ingest.counters import Counters
    from app.storage.sqlite.counters import SqliteCountersRepository

    settings = _settings(tmp_path)
    replay(SAMPLE_DATA, settings, speed=0, no_eval=True)

    conn = connect(settings)
    snap = Counters(SqliteCountersRepository(conn)).snapshot()
    conn.close()
    assert snap["duplicates"] == 1
    assert snap["late_drops"] == 1
    assert snap["validation_failures"] == 0


def test_a11_fires_via_the_real_replay_driver_not_via_events(tmp_path):
    """R11's crux, proven through the actual CLI driver (not the manual
    tick-stepping test in test_engine.py): a_11's 45-min-call rule fires
    from the interpolated clock, never from the 10:20 exit event's
    previous_state_duration_sec."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.storage.sqlite.notifications import SqliteNotificationsRepository

    settings = _settings(tmp_path)
    conn = connect(settings)
    from app.storage.db import migrate

    migrate(conn, settings)
    _seed_rules(conn, build_registry())
    conn.close()

    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)

    conn = connect(settings)
    notifications = SqliteNotificationsRepository(conn).list(subject_id="a_11")
    conn.close()
    opened = [n for n in notifications if n.transition == "opened"]
    assert len(opened) == 1
    fire_time = opened[0].event_time
    assert fire_time.startswith("2026-05-26T09:55:")  # the 09:55 minute, not 10:20
    assert "10:20" not in fire_time


def test_out_of_order_duplicate_and_late_events_do_not_corrupt_the_clock(tmp_path):
    """R10: ticks must be a pure function of event time, not of file order.
    The real sample file's duplicate (line 95, ts=09:36:30) and late event
    (line 96, ts=09:49:00) both appear textually AFTER line 94
    (ts=10:30:00) -- a naive unconditional clock.set(ts) would jump the
    engine clock backward to 09:36 while processing them, corrupting every
    subsequent on_tick's 'now' for the rest of the (already-finished)
    replay. This regression asserts no notification's event_time is
    chronologically impossible (i.e. nothing fires with a timestamp in the
    file's 09:00-10:30 range that resulted from a corrupted post-10:30
    backward-then-forward tick sequence)."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.storage.sqlite.notifications import SqliteNotificationsRepository

    settings = _settings(tmp_path)
    conn = connect(settings)
    from app.storage.db import migrate

    migrate(conn, settings)
    _seed_rules(conn, build_registry())
    conn.close()

    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)

    conn = connect(settings)
    notifications = SqliteNotificationsRepository(conn).list()
    conn.close()

    event_times = sorted(n.event_time for n in notifications)
    # Every notification's event_time must be non-decreasing when
    # notifications are considered in the order they were actually
    # dispatched (created_at order) -- a corrupted backward jump would
    # produce a later-dispatched notification with an earlier event_time
    # than one dispatched before it for the same subject/rule pair.
    by_subject: dict[str, list[str]] = {}
    for n in sorted(notifications, key=lambda n: n.created_at):
        by_subject.setdefault((n.rule_id, n.subject_id), []).append(n.event_time)
    for key, times in by_subject.items():
        assert times == sorted(times), f"{key} notification times went backward: {times}"


def test_replay_determinism_across_speeds(tmp_path):
    """Speed is purely cosmetic (a wall-clock sleep) and must never affect
    the evaluation path -- identical notification sets at speed=0
    (no sleep) and a very high speed."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.storage.sqlite.notifications import SqliteNotificationsRepository

    def run(tag, speed):
        settings = Settings(
            db_path=str(tmp_path / f"{tag}.db"), schema_path="app/storage/schema.sql",
            tick_interval_sec=5, replay_speed=10000.0,
        )
        conn = connect(settings)
        from app.storage.db import migrate

        migrate(conn, settings)
        _seed_rules(conn, build_registry())
        conn.close()
        replay(SAMPLE_DATA, settings, speed=speed, no_eval=False)
        conn = connect(settings)
        notifications = SqliteNotificationsRepository(conn).list()
        conn.close()
        return {(n.rule_id, n.subject_id, n.transition, n.event_time) for n in notifications}

    fast = run("fast", 0)  # speed<=0 -> no sleep at all
    faster = run("faster", 100000.0)
    assert fast == faster
    assert len(fast) > 0


def test_replay_twice_is_idempotent_and_does_not_resend(tmp_path, capsys):
    """Phase 6's headline claim (R9/R10): 'replaying twice produces identical
    output' backed by a test, at the real CLI level (not a unit test of one
    evaluate_rule() call). Pass two: every line is now a duplicate at the
    ingest gate, so on_change never fires and evaluate_rule is never even
    called for it -- Channel.send() sees zero new console lines, and the
    notifications table gains zero new rows."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.ingest.counters import Counters
    from app.storage.db import migrate
    from app.storage.sqlite.counters import SqliteCountersRepository
    from app.storage.sqlite.notifications import SqliteNotificationsRepository

    settings = _settings(tmp_path)
    conn = connect(settings)
    migrate(conn, settings)
    _seed_rules(conn, build_registry())
    conn.close()

    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)
    capsys.readouterr()  # discard pass-one console output

    conn = connect(settings)
    first_pass = sorted(
        (n.id, n.rule_id, n.subject_id, n.transition, n.occurrence_seq)
        for n in SqliteNotificationsRepository(conn).list()
    )
    duplicates_before = Counters(SqliteCountersRepository(conn)).get("duplicates")
    conn.close()
    assert len(first_pass) > 0

    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)
    captured = capsys.readouterr()

    conn = connect(settings)
    second_pass = sorted(
        (n.id, n.rule_id, n.subject_id, n.transition, n.occurrence_seq)
        for n in SqliteNotificationsRepository(conn).list()
    )
    duplicates_after = Counters(SqliteCountersRepository(conn)).get("duplicates")
    conn.close()

    assert second_pass == first_pass  # same rows, same ids -- nothing new inserted
    assert duplicates_after - duplicates_before == 96  # every line is now a dup
    notif_lines = [line for line in captured.out.splitlines() if line.startswith("[")]
    assert notif_lines == []  # ConsoleChannel.send() produces exactly these lines


def test_replay_twice_does_not_double_count_evaluations_suppressed(tmp_path):
    """Regression: the notification-level idempotency key (occurrence_seq)
    doesn't cover suppressed evaluations, since a suppressed evaluation
    produces no notification at all. Before the replay_watermark fix, a
    second full replay() re-walked ticks past every still-active episode's
    last_notified_at (an episode-derived floor can't tell 'nothing further
    happened' apart from 'ticks kept running and silently incrementing
    evaluations_suppressed'), inflating the count on every repeat run --
    confirmed concretely at 240 -> 359 before this fix. The watermark
    (an explicit 'ticked through here already' fact, not a proxy) closes it."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.storage.db import migrate
    from app.storage.sqlite.episodes import SqliteEpisodesRepository

    settings = _settings(tmp_path)
    conn = connect(settings)
    migrate(conn, settings)
    _seed_rules(conn, build_registry())
    conn.close()

    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)
    conn = connect(settings)
    before = {e.id: e.evaluations_suppressed for e in SqliteEpisodesRepository(conn).list_active()}
    conn.close()
    assert before  # sanity: the sample data does leave active episodes at file end

    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)
    conn = connect(settings)
    after = {e.id: e.evaluations_suppressed for e in SqliteEpisodesRepository(conn).list_active()}
    conn.close()

    assert after == before


def test_no_eval_pass_does_not_poison_watermark_for_a_later_eval_pass(tmp_path):
    """Regression (code-review CRITICAL): a --no-eval pass still walks the
    clock to the file's final timestamp (only the on_tick() call itself is
    skipped), so writing the watermark unconditionally meant a no_eval
    ingest-only pass, followed by a real evaluation pass into the SAME db
    (a legitimate sequence: pre-ingest to seed counters, then run the
    engine for real), silently produced zero evaluations and zero
    notifications -- every event satisfied ts < clock.now() from the start,
    so both step_to() and clock.set() were skipped for the entire file, and
    on_change never fired since the events are storage-layer duplicates.
    No error, no warning -- just silence."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.ingest.counters import Counters
    from app.storage.db import migrate
    from app.storage.sqlite.counters import SqliteCountersRepository
    from app.storage.sqlite.notifications import SqliteNotificationsRepository

    settings = _settings(tmp_path)
    conn = connect(settings)
    migrate(conn, settings)
    _seed_rules(conn, build_registry())
    conn.close()

    replay(SAMPLE_DATA, settings, speed=0, no_eval=True)
    replay(SAMPLE_DATA, settings, speed=0, no_eval=False)

    conn = connect(settings)
    notifications = SqliteNotificationsRepository(conn).list()
    evaluations = Counters(SqliteCountersRepository(conn)).snapshot()
    conn.close()

    assert len(notifications) > 0
    assert evaluations["evaluations_event"] + evaluations["evaluations_due"] > 0


def test_crash_recovery_partial_then_full_replay_matches_clean_run(tmp_path):
    """Phase 6 crash-recovery test: replay a truncated prefix (simulating a
    crash mid-stream), then replay the FULL file from the start into the
    SAME database (simulating a restart that re-reads the whole log rather
    than resuming from an offset) -- the final notification set must equal
    a clean, single, full run's set. This only holds because of the
    idempotent-send gating above: events 1-50 are duplicates on the second
    pass and never re-evaluated, but the episodes they already opened
    persist, so the tail of the file (51-96) continues the same state
    machine a clean run would have reached by that point."""
    from app.cli.replay import replay
    from app.evaluation.registry import build_registry
    from app.storage.db import migrate
    from app.storage.sqlite.notifications import SqliteNotificationsRepository

    def notification_set(settings) -> set:
        conn = connect(settings)
        notifications = SqliteNotificationsRepository(conn).list()
        conn.close()
        return {(n.rule_id, n.subject_id, n.transition, n.event_time) for n in notifications}

    # Clean, single full run -- the reference.
    clean_settings = Settings(
        db_path=str(tmp_path / "clean.db"), schema_path="app/storage/schema.sql",
        tick_interval_sec=5, replay_speed=10000.0,
    )
    conn = connect(clean_settings)
    migrate(conn, clean_settings)
    _seed_rules(conn, build_registry())
    conn.close()
    replay(SAMPLE_DATA, clean_settings, speed=0, no_eval=False)
    clean_result = notification_set(clean_settings)
    assert len(clean_result) > 0

    # Crash-recovery run: first 50 lines, tear down, then the full 96 from the start.
    recovery_settings = Settings(
        db_path=str(tmp_path / "recovery.db"), schema_path="app/storage/schema.sql",
        tick_interval_sec=5, replay_speed=10000.0,
    )
    conn = connect(recovery_settings)
    migrate(conn, recovery_settings)
    _seed_rules(conn, build_registry())
    conn.close()

    all_lines = SAMPLE_DATA.read_text().splitlines()
    prefix_path = tmp_path / "prefix.jsonl"
    prefix_path.write_text("\n".join(all_lines[:50]) + "\n")

    replay(prefix_path, recovery_settings, speed=0, no_eval=False)
    replay(SAMPLE_DATA, recovery_settings, speed=0, no_eval=False)
    recovery_result = notification_set(recovery_settings)

    assert recovery_result == clean_result
