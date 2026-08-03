from app.storage.sqlite.counters import SqliteCountersRepository


def test_get_missing_counter_returns_zero(db_conn):
    repo = SqliteCountersRepository(db_conn)
    assert repo.get("duplicates") == 0


def test_increment_creates_and_accumulates(db_conn):
    repo = SqliteCountersRepository(db_conn)
    repo.increment("duplicates")
    repo.increment("duplicates")
    repo.increment("duplicates", by=3)
    assert repo.get("duplicates") == 5


def test_all_returns_every_counter(db_conn):
    repo = SqliteCountersRepository(db_conn)
    repo.increment("duplicates")
    repo.increment("late_drops", by=2)
    assert repo.all() == {"duplicates": 1, "late_drops": 2}
