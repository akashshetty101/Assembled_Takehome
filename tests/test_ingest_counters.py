import pytest

from app.ingest.counters import COUNTER_NAMES, Counters
from app.storage.sqlite.counters import SqliteCountersRepository


def test_increment_and_get_known_counter(db_conn):
    counters = Counters(SqliteCountersRepository(db_conn))
    counters.increment("duplicates")
    counters.increment("duplicates")
    assert counters.get("duplicates") == 2


def test_increment_rejects_unknown_name(db_conn):
    counters = Counters(SqliteCountersRepository(db_conn))
    with pytest.raises(ValueError):
        counters.increment("not_a_real_counter")


def test_get_rejects_unknown_name(db_conn):
    counters = Counters(SqliteCountersRepository(db_conn))
    with pytest.raises(ValueError):
        counters.get("not_a_real_counter")


def test_get_missing_known_counter_defaults_to_zero(db_conn):
    counters = Counters(SqliteCountersRepository(db_conn))
    assert counters.get("late_drops") == 0


def test_snapshot_returns_every_fixed_counter_name(db_conn):
    counters = Counters(SqliteCountersRepository(db_conn))
    counters.increment("validation_failures", by=3)
    snapshot = counters.snapshot()
    assert set(snapshot) == set(COUNTER_NAMES)
    assert snapshot["validation_failures"] == 3
    assert snapshot["duplicates"] == 0
