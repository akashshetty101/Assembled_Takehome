from app.storage.repositories import RuleRecord
from app.storage.sqlite.rules import SqliteRulesRepository


def _rule(rule_id="r1", enabled=True):
    return RuleRecord(
        rule_id, "name", "queue", "{}", "[]", 300, 0, "author", None, "t", enabled,
        "lead_sam", "2026-05-26T09:00:00Z", 1,
    )


def test_create_then_get_round_trips(db_conn):
    repo = SqliteRulesRepository(db_conn)
    repo.create(_rule())
    assert repo.get("r1") == _rule()


def test_list_enabled_only_filters(db_conn):
    repo = SqliteRulesRepository(db_conn)
    repo.create(_rule("r1", enabled=True))
    repo.create(_rule("r2", enabled=False))
    assert {r.id for r in repo.list(enabled_only=True)} == {"r1"}
    assert {r.id for r in repo.list()} == {"r1", "r2"}


def test_update_bumps_version(db_conn):
    repo = SqliteRulesRepository(db_conn)
    repo.create(_rule())
    updated = RuleRecord(
        "r1", "renamed", "queue", "{}", "[]", 300, 0, "author", None, "t", False,
        "lead_sam", "2026-05-26T09:00:00Z", 2,
    )
    repo.update(updated)
    assert repo.get("r1") == updated
