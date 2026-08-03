import sqlite3

import pytest

from app.storage.repositories import EventRecord
from app.storage.sqlite.events import SqliteEventsRepository


def test_append_then_get_round_trips(db_conn):
    repo = SqliteEventsRepository(db_conn)
    record = EventRecord("evt_1", "2026-05-26T09:00:00Z", "queue_snapshot", '{"a":1}', 1)
    repo.append(record)
    assert repo.get("evt_1") == record


def test_exists_false_before_append_true_after(db_conn):
    repo = SqliteEventsRepository(db_conn)
    assert repo.exists("evt_1") is False
    repo.append(EventRecord("evt_1", "2026-05-26T09:00:00Z", "queue_snapshot", "{}", 1))
    assert repo.exists("evt_1") is True


def test_get_returns_none_for_missing(db_conn):
    repo = SqliteEventsRepository(db_conn)
    assert repo.get("nope") is None


def test_append_duplicate_event_id_raises(db_conn):
    """events.event_id is PRIMARY KEY -- dedup at the storage layer (schema.sql)."""
    repo = SqliteEventsRepository(db_conn)
    repo.append(EventRecord("evt_1", "2026-05-26T09:00:00Z", "queue_snapshot", "{}", 1))
    with pytest.raises(sqlite3.IntegrityError):
        repo.append(EventRecord("evt_1", "2026-05-26T09:00:01Z", "queue_snapshot", "{}", 2))
