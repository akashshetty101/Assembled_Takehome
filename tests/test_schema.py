"""Schema-only tests: load schema.sql directly with sqlite3, no app.storage.db
yet (that module doesn't exist until the next Phase 0 step). These prove the
DDL's own constraints hold, independent of the connection factory."""
import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "app" / "storage" / "schema.sql"

EXPECTED_TABLES = {
    "events",
    "queue_state",
    "agent_state",
    "rules",
    "episodes",
    "notifications",
    "ingest_counters",
}


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text())
    yield connection
    connection.close()


def test_schema_file_exists():
    assert SCHEMA_PATH.exists()


def test_all_expected_tables_exist(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES <= names


def test_schema_is_idempotent_to_reapply(conn):
    # CREATE TABLE/INDEX IF NOT EXISTS must tolerate being run twice.
    conn.executescript(SCHEMA_PATH.read_text())


def _insert_rule(conn, rule_id="r1", recipient_kind="author", recipient_target=None):
    conn.execute(
        """
        INSERT INTO rules (id, name, subject_type, selector_json, conditions_json,
                            for_duration_sec, cooldown_sec, recipient_kind,
                            recipient_target, template, enabled, created_by, created_at, version)
        VALUES (?, 'r', 'queue', '{}', '[]', 0, 0, ?, ?, 't', 1, 'lead_sam', '2026-05-26T09:00:00Z', 1)
        """,
        (rule_id, recipient_kind, recipient_target),
    )


def _insert_episode(conn, episode_id, rule_id, subject_id, state):
    conn.execute(
        """
        INSERT INTO episodes (id, rule_id, subject_id, state, first_true_at, opened_at,
                               closed_at, last_notified_at, notify_seq, evaluations_suppressed, stale)
        VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, 0, 0)
        """,
        (episode_id, rule_id, subject_id, state),
    )


def test_events_primary_key_rejects_duplicate_event_id(conn):
    conn.execute(
        "INSERT INTO events (event_id, ts, type, payload_json, received_seq) "
        "VALUES ('evt_1', '2026-05-26T09:00:00Z', 'queue_snapshot', '{}', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events (event_id, ts, type, payload_json, received_seq) "
            "VALUES ('evt_1', '2026-05-26T09:00:01Z', 'queue_snapshot', '{}', 2)"
        )


def test_partial_unique_index_rejects_second_open_episode(conn):
    """R9 / Phase 0: the DB, not application code, must be the backstop against
    two concurrently open episodes for the same (rule, subject)."""
    _insert_rule(conn)
    _insert_episode(conn, "ep1", "r1", "billing", "open")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_episode(conn, "ep2", "r1", "billing", "pending")


def test_partial_unique_index_allows_clear_episode_alongside_open_history(conn):
    """Closing episode 1 (state='clear') then opening a new one is the re-open
    path (Phase 3) and must not be blocked by the partial index."""
    _insert_rule(conn)
    _insert_episode(conn, "ep1", "r1", "billing", "clear")
    _insert_episode(conn, "ep2", "r1", "billing", "open")


def _insert_notification(conn, notif_id, episode_id, transition, occurrence_seq):
    conn.execute(
        """
        INSERT INTO notifications (id, episode_id, rule_id, subject_id, transition,
                                    occurrence_seq, recipient_kind, recipient_target,
                                    body, facts_snapshot_json, event_time, created_at)
        VALUES (?, ?, 'r1', 'billing', ?, ?, 'author', 'lead_sam', 'body', '{}',
                '2026-05-26T09:36:00Z', '2026-05-26T09:36:00Z')
        """,
        (notif_id, episode_id, transition, occurrence_seq),
    )


def test_notification_idempotency_key_includes_occurrence_seq(conn):
    """R9: the unique key is (episode_id, transition, occurrence_seq), not just
    (episode_id, transition) -- reminders share a transition value and must be
    distinguished by occurrence_seq."""
    _insert_rule(conn)
    _insert_episode(conn, "ep1", "r1", "billing", "open")
    _insert_notification(conn, "n1", "ep1", "reminder", 1)
    # A second reminder with a different occurrence_seq must be allowed.
    _insert_notification(conn, "n2", "ep1", "reminder", 2)
    # But repeating the same (episode_id, transition, occurrence_seq) must fail.
    with pytest.raises(sqlite3.IntegrityError):
        _insert_notification(conn, "n3", "ep1", "reminder", 2)


def test_rules_reject_negative_for_duration(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO rules (id, name, subject_type, selector_json, conditions_json,
                                for_duration_sec, cooldown_sec, recipient_kind,
                                recipient_target, template, enabled, created_by, created_at, version)
            VALUES ('bad', 'r', 'queue', '{}', '[]', -1, 0, 'author', NULL, 't', 1, 'lead_sam', '2026-05-26T09:00:00Z', 1)
            """
        )


def test_rules_reject_channel_recipient_without_target(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_rule(conn, recipient_kind="channel", recipient_target=None)


def test_rules_reject_unknown_subject_type(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO rules (id, name, subject_type, selector_json, conditions_json,
                                for_duration_sec, cooldown_sec, recipient_kind,
                                recipient_target, template, enabled, created_by, created_at, version)
            VALUES ('bad', 'r', 'not_a_subject', '{}', '[]', 0, 0, 'author', NULL, 't', 1, 'lead_sam', '2026-05-26T09:00:00Z', 1)
            """
        )


def test_episodes_reject_unknown_state(conn):
    _insert_rule(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_episode(conn, "ep1", "r1", "billing", "bogus")


def test_episodes_rule_id_foreign_key_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_episode(conn, "ep1", "no_such_rule", "billing", "open")


def test_ingest_counters_round_trip(conn):
    conn.execute("INSERT INTO ingest_counters (name, value) VALUES ('duplicates', 0)")
    conn.execute("UPDATE ingest_counters SET value = value + 1 WHERE name = 'duplicates'")
    row = conn.execute("SELECT value FROM ingest_counters WHERE name = 'duplicates'").fetchone()
    assert row[0] == 1
