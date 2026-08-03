import sqlite3

import pytest

from app.config import Settings
from app.storage.db import connect, migrate, transaction

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
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "test.db"), schema_path="app/storage/schema.sql")


def test_connect_creates_parent_dir_and_file(settings):
    conn = connect(settings)
    try:
        migrate(conn, settings)
    finally:
        conn.close()
    from pathlib import Path

    assert Path(settings.db_path).exists()


def test_connect_enables_foreign_keys(settings):
    conn = connect(settings)
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


def test_connect_enables_wal_journal_mode(settings):
    conn = connect(settings)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].lower() == "wal"
    finally:
        conn.close()


def test_connect_uses_row_factory_for_column_access(settings):
    conn = connect(settings)
    try:
        migrate(conn, settings)
        conn.execute(
            "INSERT INTO ingest_counters (name, value) VALUES ('duplicates', 3)"
        )
        row = conn.execute(
            "SELECT name, value FROM ingest_counters WHERE name = 'duplicates'"
        ).fetchone()
        assert row["name"] == "duplicates"
        assert row["value"] == 3
    finally:
        conn.close()


def test_migrate_creates_all_tables(settings):
    conn = connect(settings)
    try:
        migrate(conn, settings)
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r["name"] for r in rows}
        assert EXPECTED_TABLES <= names
    finally:
        conn.close()


def test_migrate_is_idempotent_when_run_twice(settings):
    conn = connect(settings)
    try:
        migrate(conn, settings)
        migrate(conn, settings)  # must not raise
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r["name"] for r in rows}
        assert EXPECTED_TABLES <= names
    finally:
        conn.close()


def test_migrate_persists_across_reconnect(settings):
    conn = connect(settings)
    try:
        migrate(conn, settings)
        conn.execute("INSERT INTO ingest_counters (name, value) VALUES ('late_drops', 1)")
        conn.commit()
    finally:
        conn.close()

    conn2 = connect(settings)
    try:
        row = conn2.execute(
            "SELECT value FROM ingest_counters WHERE name = 'late_drops'"
        ).fetchone()
        assert row["value"] == 1
    finally:
        conn2.close()


def test_transaction_commits_on_success_and_is_visible_after_reconnect(settings):
    conn = connect(settings)
    migrate(conn, settings)
    with transaction(conn):
        conn.execute("INSERT INTO ingest_counters (name, value) VALUES ('duplicates', 1)")
    conn.close()

    reconnected = connect(settings)
    try:
        row = reconnected.execute(
            "SELECT value FROM ingest_counters WHERE name = 'duplicates'"
        ).fetchone()
        assert row["value"] == 1
    finally:
        reconnected.close()


def test_transaction_rolls_back_all_statements_on_exception(settings):
    """R4: a mid-batch failure must not leave a partial write. Two inserts in
    one transaction; the second violates the PRIMARY KEY, so neither must
    survive -- proving rollback is atomic across the whole block, not just
    the failing statement."""
    conn = connect(settings)
    migrate(conn, settings)
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute("INSERT INTO ingest_counters (name, value) VALUES ('late_drops', 1)")
            conn.execute("INSERT INTO ingest_counters (name, value) VALUES ('late_drops', 2)")
    conn.close()

    reconnected = connect(settings)
    try:
        row = reconnected.execute(
            "SELECT value FROM ingest_counters WHERE name = 'late_drops'"
        ).fetchone()
        assert row is None
    finally:
        reconnected.close()


def test_transaction_spans_multiple_repository_writes_atomically(settings):
    """The R4 scenario itself: event append + subject-state watermark update
    as one atomic unit, using the real repository classes (not raw SQL)."""
    from datetime import datetime, timezone

    from app.domain.subjects import QueueState
    from app.storage.repositories import EventRecord
    from app.storage.sqlite.events import SqliteEventsRepository
    from app.storage.sqlite.subject_state import SqliteQueueStateRepository

    conn = connect(settings)
    migrate(conn, settings)
    events_repo = SqliteEventsRepository(conn)
    queue_repo = SqliteQueueStateRepository(conn)

    with transaction(conn):
        events_repo.append(
            EventRecord("evt_1", "2026-05-26T09:00:00Z", "queue_snapshot", "{}", 1)
        )
        queue_repo.put(
            QueueState(queue_id="billing", last_event_ts=datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
        )
    conn.close()

    reconnected = connect(settings)
    try:
        assert SqliteEventsRepository(reconnected).exists("evt_1")
        assert SqliteQueueStateRepository(reconnected).get("billing") is not None
    finally:
        reconnected.close()
