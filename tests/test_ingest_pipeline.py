import json
import logging
from pathlib import Path

import pytest

from app.domain.subjects import SubjectType
from app.ingest.counters import Counters
from app.ingest.pipeline import ingest_event
from app.storage.sqlite.events import SqliteEventsRepository
from app.storage.sqlite.subject_state import SqliteAgentStateRepository, SqliteQueueStateRepository
from app.storage.sqlite.counters import SqliteCountersRepository

SAMPLE_DATA = Path(__file__).resolve().parent.parent / "Sample_Data" / "events.jsonl"


@pytest.fixture
def repos(db_conn):
    return {
        "conn": db_conn,
        "events_repo": SqliteEventsRepository(db_conn),
        "counters": Counters(SqliteCountersRepository(db_conn)),
        "queue_repo": SqliteQueueStateRepository(db_conn),
        "agent_repo": SqliteAgentStateRepository(db_conn),
    }


def _ingest(repos, raw, seq):
    return ingest_event(raw, received_seq=seq, **repos)


BILLING_SNAPSHOT_1 = {
    "event_id": "evt_01HXYZ001", "ts": "2026-05-26T09:00:00Z", "type": "queue_snapshot",
    "queue_id": "billing", "tickets_waiting": 0, "longest_wait_sec": 0, "sla_target_sec": 120,
    "agents_available": 0, "agents_on_call": 0, "volume_last_15m": 6, "volume_forecast_next_15m": 22,
}


def test_validation_failure_persists_nothing(repos):
    outcome = _ingest(repos, {"event_id": "evt_bad", "ts": "2026-05-26T09:00:00Z", "type": "nope"}, 1)
    assert outcome.accepted is False
    assert outcome.changed_subject is None
    assert repos["counters"].get("validation_failures") == 1
    assert repos["events_repo"].exists("evt_bad") is False


def test_normal_event_accepted_and_projected(repos):
    outcome = _ingest(repos, BILLING_SNAPSHOT_1, 1)
    assert outcome.accepted is True
    assert outcome.changed_subject.subject_type == SubjectType.QUEUE
    assert outcome.changed_subject.subject_id == "billing"
    assert repos["events_repo"].exists("evt_01HXYZ001") is True
    assert repos["queue_repo"].get("billing").tickets_waiting == 0
    assert repos["counters"].get("events_accepted") == 1


def test_late_event_increments_late_drops_not_events_accepted(repos):
    """Phase 6 reconciliation: events_accepted, duplicates, late_drops, and
    validation_failures must be mutually-exclusive partitions of every
    ingested line. A late event is written to the events table (R5) but
    counts only toward late_drops, not also toward events_accepted --
    otherwise the four counters would double-count it and never sum to
    lines_read."""
    early = {**BILLING_SNAPSHOT_1, "event_id": "evt_a", "ts": "2026-05-26T09:30:00Z"}
    late = {**BILLING_SNAPSHOT_1, "event_id": "evt_b", "ts": "2026-05-26T09:00:00Z"}
    _ingest(repos, early, 1)
    _ingest(repos, late, 2)
    assert repos["counters"].get("events_accepted") == 1
    assert repos["counters"].get("late_drops") == 1


def test_duplicate_and_validation_failure_do_not_increment_events_accepted(repos):
    _ingest(repos, BILLING_SNAPSHOT_1, 1)
    _ingest(repos, BILLING_SNAPSHOT_1, 2)  # duplicate
    _ingest(repos, {"event_id": "evt_bad", "ts": "2026-05-26T09:00:00Z", "type": "nope"}, 3)
    assert repos["counters"].get("events_accepted") == 1


def test_dedup_first_write_wins_and_flags_payload_mismatch(repos, caplog):
    original = {
        "event_id": "evt_01HXYZ050", "ts": "2026-05-26T09:36:00Z", "type": "adherence_check",
        "agent_id": "a_19", "queue_ids": ["billing"], "scheduled_state": "available",
        "actual_state": "on_break", "in_violation": True, "violation_started_at": "2026-05-26T09:35:00Z",
    }
    duplicate_with_different_ts = {**original, "ts": "2026-05-26T09:36:30Z"}

    _ingest(repos, original, 1)
    with caplog.at_level(logging.WARNING):
        outcome = _ingest(repos, duplicate_with_different_ts, 2)

    assert outcome.accepted is False
    assert repos["counters"].get("duplicates") == 1
    assert repos["counters"].get("duplicate_payload_mismatch") == 1
    assert any("evt_01HXYZ050" in r.message for r in caplog.records)
    stored = repos["events_repo"].get("evt_01HXYZ050")
    assert json.loads(stored.payload_json)["ts"] == "2026-05-26T09:36:00Z"  # first-write-wins


def test_on_change_callback_fires_only_on_normal_accepted_path(repos):
    """Phase 4 item 9: on_event wires in via a callback, not an import --
    ingest/pipeline.py has no import of app.engine anywhere. Fires only for
    the normal projected path, never for duplicates/validation
    failures/late events."""
    calls = []

    def on_change(ref, ts):
        calls.append((ref.subject_id, ts))

    ingest_event(BILLING_SNAPSHOT_1, received_seq=1, on_change=on_change, **repos)
    assert len(calls) == 1
    assert calls[0][0] == "billing"

    calls.clear()
    ingest_event(BILLING_SNAPSHOT_1, received_seq=2, on_change=on_change, **repos)  # duplicate
    assert calls == []

    calls.clear()
    late = {**BILLING_SNAPSHOT_1, "event_id": "evt_late", "ts": "2026-05-26T08:00:00Z"}
    ingest_event(late, received_seq=3, on_change=on_change, **repos)
    assert calls == []


def test_ingest_pipeline_does_not_import_engine():
    from pathlib import Path

    source = Path("app/ingest/pipeline.py").read_text()
    assert "app.engine" not in source


def test_late_event_still_appended_to_log_but_skips_projection(repos):
    early = {**BILLING_SNAPSHOT_1, "event_id": "evt_a", "ts": "2026-05-26T09:30:00Z", "tickets_waiting": 18}
    late = {**BILLING_SNAPSHOT_1, "event_id": "evt_b", "ts": "2026-05-26T09:00:00Z", "tickets_waiting": 999}

    _ingest(repos, early, 1)
    outcome = _ingest(repos, late, 2)

    assert outcome.accepted is True
    assert outcome.changed_subject is None
    assert repos["counters"].get("late_drops") == 1
    assert repos["events_repo"].exists("evt_b") is True  # R5: still logged
    assert repos["queue_repo"].get("billing").tickets_waiting == 18  # projection untouched


def test_equal_timestamp_is_accepted_not_late(repos):
    first = {**BILLING_SNAPSHOT_1, "event_id": "evt_a", "tickets_waiting": 1}
    second = {**BILLING_SNAPSHOT_1, "event_id": "evt_b", "tickets_waiting": 2}
    _ingest(repos, first, 1)
    outcome = _ingest(repos, second, 2)
    assert outcome.accepted is True
    assert outcome.changed_subject is not None
    assert repos["counters"].get("late_drops") == 0
    assert repos["queue_repo"].get("billing").tickets_waiting == 2


def test_dedup_precedes_watermark_ordering(repos):
    """R3: a duplicate that is ALSO late (line 95 in the real file) must be
    counted as a duplicate, not a late_drop -- proving the gate order."""
    original = {
        "event_id": "evt_01HXYZ050", "ts": "2026-05-26T09:36:00Z", "type": "adherence_check",
        "agent_id": "a_19", "queue_ids": ["billing"], "scheduled_state": "available",
        "actual_state": "on_break", "in_violation": True, "violation_started_at": "2026-05-26T09:35:00Z",
    }
    later_event = {
        "event_id": "evt_01HXYZ067", "ts": "2026-05-26T09:55:00Z", "type": "adherence_check",
        "agent_id": "a_19", "queue_ids": ["billing"], "scheduled_state": "available",
        "actual_state": "on_break", "in_violation": True, "violation_started_at": "2026-05-26T09:35:00Z",
    }
    duplicate_and_late = {**original, "ts": "2026-05-26T09:36:30Z"}

    _ingest(repos, original, 1)
    _ingest(repos, later_event, 2)  # advances a_19's watermark to 09:55
    outcome = _ingest(repos, duplicate_and_late, 3)  # ts 09:36:30 < watermark -- also late

    assert outcome.accepted is False
    assert repos["counters"].get("duplicates") == 1
    assert repos["counters"].get("late_drops") == 0  # NOT counted as late


def test_queue_ids_null_and_empty_list_both_normalize_to_empty(repos):
    null_variant = {
        "event_id": "evt_073", "ts": "2026-05-26T10:01:40Z", "type": "agent_state_change",
        "agent_id": "a_05", "queue_ids": None, "previous_state": "available",
        "previous_state_duration_sec": 3610, "new_state": "on_call",
    }
    empty_list_variant = {
        "event_id": "evt_074", "ts": "2026-05-26T10:02:00Z", "type": "adherence_check",
        "agent_id": "a_05", "queue_ids": [], "scheduled_state": "available",
        "actual_state": "on_call", "in_violation": False, "violation_started_at": None,
    }
    _ingest(repos, null_variant, 1)
    assert repos["agent_repo"].get("a_05").queue_ids == []
    _ingest(repos, empty_list_variant, 2)
    assert repos["agent_repo"].get("a_05").queue_ids == []


def test_null_forecast_projects_without_error(repos):
    event = {**BILLING_SNAPSHOT_1, "event_id": "evt_068", "volume_forecast_next_15m": None}
    _ingest(repos, event, 1)
    assert repos["queue_repo"].get("billing").volume_forecast_next_15m is None


def test_unknowable_violation_start_and_state_disagreement(repos, caplog):
    """evt_01HXYZ086: a_23's current_state is on_call (from a prior
    agent_state_change); this adherence_check reports actual_state=in_meeting
    with violation_started_at=null -- both R7's disagreement and the
    unknown-violation-start case fire on the same event."""
    prior_state_change = {
        "event_id": "evt_053", "ts": "2026-05-26T09:45:00Z", "type": "agent_state_change",
        "agent_id": "a_23", "queue_ids": ["tier_2"], "previous_state": "available",
        "previous_state_duration_sec": 1020, "new_state": "on_call",
    }
    disagreement_event = {
        "event_id": "evt_086", "ts": "2026-05-26T10:15:30Z", "type": "adherence_check",
        "agent_id": "a_23", "queue_ids": ["tier_2"], "scheduled_state": "available",
        "actual_state": "in_meeting", "in_violation": True, "violation_started_at": None,
    }
    _ingest(repos, prior_state_change, 1)
    with caplog.at_level(logging.WARNING):
        _ingest(repos, disagreement_event, 2)

    state = repos["agent_repo"].get("a_23")
    assert state.violation_active is True
    assert state.violation_started_at is None
    assert state.violation_start_source.value == "unknown"
    assert state.current_state == "on_call"  # unchanged -- R7
    assert repos["counters"].get("state_disagreement") == 1
    assert any("a_23" in r.message for r in caplog.records)


def _connect_migrated(settings):
    from app.storage.db import connect, migrate

    conn = connect(settings)
    migrate(conn, settings)
    return conn


def _repos_for(conn):
    return {
        "conn": conn,
        "events_repo": SqliteEventsRepository(conn),
        "counters": Counters(SqliteCountersRepository(conn)),
        "queue_repo": SqliteQueueStateRepository(conn),
        "agent_repo": SqliteAgentStateRepository(conn),
    }


def test_validation_failure_counter_survives_reconnect_with_no_other_events(tmp_path):
    """Regression for the code-review CRITICAL: a validation-failure-only
    ingest run (no other event on the connection to incidentally flush a
    later transaction) must still commit its counter increment."""
    from app.config import Settings

    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql")
    conn = _connect_migrated(settings)
    _ingest(_repos_for(conn), {"event_id": "evt_bad", "ts": "2026-05-26T09:00:00Z", "type": "nope"}, 1)
    conn.close()

    reconnected = _connect_migrated(settings)
    assert Counters(SqliteCountersRepository(reconnected)).get("validation_failures") == 1
    reconnected.close()


def test_duplicate_only_counters_survive_reconnect_with_no_other_events(tmp_path):
    """Same regression, for the duplicate/duplicate_payload_mismatch path:
    ingest the original, then ONLY a mismatched duplicate (no third event),
    close without any other write, and confirm both counters persisted."""
    from app.config import Settings

    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql")
    conn = _connect_migrated(settings)
    repos = _repos_for(conn)
    _ingest(repos, BILLING_SNAPSHOT_1, 1)
    _ingest(repos, {**BILLING_SNAPSHOT_1, "tickets_waiting": 999}, 2)  # duplicate, mismatched payload
    conn.close()

    reconnected = _connect_migrated(settings)
    counters = Counters(SqliteCountersRepository(reconnected))
    assert counters.get("duplicates") == 1
    assert counters.get("duplicate_payload_mismatch") == 1
    reconnected.close()


def test_full_file_integration():
    """The Phase 1 'Green at end' proof, without a not-yet-built CLI: replay
    the real 96-line file through the pipeline directly and check the exact
    counters PLAN.md's own Tests section specifies."""
    from app.config import Settings

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(db_path=f"{tmp}/test.db", schema_path="app/storage/schema.sql")
        conn = _connect_migrated(settings)
        repos = _repos_for(conn)

        lines = SAMPLE_DATA.read_text().splitlines()
        assert len(lines) == 96

        projected = 0
        for seq, line in enumerate(lines, start=1):
            raw = json.loads(line)
            outcome = _ingest(repos, raw, seq)
            if outcome.changed_subject is not None:
                projected += 1

        assert projected == 94
        duplicates = repos["counters"].get("duplicates")
        late_drops = repos["counters"].get("late_drops")
        validation_failures = repos["counters"].get("validation_failures")
        events_accepted = repos["counters"].get("events_accepted")
        assert duplicates == 1
        assert late_drops == 1
        assert validation_failures == 0
        assert repos["queue_repo"].count() == 3
        assert repos["agent_repo"].count() == 8

        # Phase 6's counter-reconciliation invariant, proven here already.
        assert events_accepted + duplicates + late_drops + validation_failures == len(lines)

        conn.close()
