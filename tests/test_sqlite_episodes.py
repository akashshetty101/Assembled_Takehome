import sqlite3

import pytest

from app.storage.repositories import EpisodeRecord, RuleRecord
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.rules import SqliteRulesRepository


def _seed_rule(db_conn, rule_id="r1"):
    SqliteRulesRepository(db_conn).create(
        RuleRecord(rule_id, "n", "queue", "{}", "[]", 0, 0, "author", None, "t", True,
                    "lead_sam", "2026-05-26T09:00:00Z", 1)
    )


def test_create_then_get_round_trips(db_conn):
    _seed_rule(db_conn)
    repo = SqliteEpisodesRepository(db_conn)
    record = EpisodeRecord("ep1", "r1", "billing", "open", None, "2026-05-26T09:36:00Z",
                            None, None, 0, 0, False)
    repo.create(record)
    assert repo.get("ep1") == record


def test_get_active_finds_pending_or_open_not_clear(db_conn):
    _seed_rule(db_conn)
    repo = SqliteEpisodesRepository(db_conn)
    repo.create(EpisodeRecord("ep1", "r1", "billing", "clear", None, None, None, None, 0, 0, False))
    assert repo.get_active("r1", "billing") is None
    repo.create(EpisodeRecord("ep2", "r1", "tier_2", "pending", "2026-05-26T10:00:00Z",
                               None, None, None, 0, 0, False))
    assert repo.get_active("r1", "tier_2").id == "ep2"


def test_update_changes_state(db_conn):
    _seed_rule(db_conn)
    repo = SqliteEpisodesRepository(db_conn)
    repo.create(EpisodeRecord("ep1", "r1", "billing", "open", None, "09:36", None, None, 0, 0, False))
    resolved = EpisodeRecord("ep1", "r1", "billing", "clear", None, "09:36", "10:15", None, 0, 5, False)
    repo.update(resolved)
    assert repo.get("ep1") == resolved


def test_list_active_excludes_clear_episodes(db_conn):
    _seed_rule(db_conn)
    repo = SqliteEpisodesRepository(db_conn)
    repo.create(EpisodeRecord("ep1", "r1", "billing", "clear", None, None, "10:00", None, 0, 0, False))
    repo.create(EpisodeRecord("ep2", "r1", "tier_2", "pending", "10:00", None, None, None, 0, 0, False))
    active_ids = {e.id for e in repo.list_active()}
    assert active_ids == {"ep2"}


def test_partial_index_enforced_through_repo(db_conn):
    """R9/R12: the DB rejects a second open episode even via the repo API."""
    _seed_rule(db_conn)
    repo = SqliteEpisodesRepository(db_conn)
    repo.create(EpisodeRecord("ep1", "r1", "billing", "open", None, "09:36", None, None, 0, 0, False))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(EpisodeRecord("ep2", "r1", "billing", "pending", "09:40", None, None, None, 0, 0, False))
