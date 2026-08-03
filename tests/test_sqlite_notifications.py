from app.storage.repositories import EpisodeRecord, NotificationRecord, RuleRecord
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.notifications import SqliteNotificationsRepository
from app.storage.sqlite.rules import SqliteRulesRepository


def _seed(db_conn):
    SqliteRulesRepository(db_conn).create(
        RuleRecord("r1", "n", "queue", "{}", "[]", 0, 0, "author", None, "t", True,
                    "lead_sam", "2026-05-26T09:00:00Z", 1)
    )
    SqliteEpisodesRepository(db_conn).create(
        EpisodeRecord("ep1", "r1", "billing", "open", None, "09:36", None, None, 0, 0, False)
    )


def _notif(occurrence_seq=0, transition="opened", notif_id="n1"):
    return NotificationRecord(
        notif_id, "ep1", "r1", "billing", transition, occurrence_seq, "author", "lead_sam",
        "body", "{}", "2026-05-26T09:36:00Z", "2026-05-26T09:36:00Z",
    )


def test_insert_if_absent_returns_true_on_first_insert(db_conn):
    _seed(db_conn)
    repo = SqliteNotificationsRepository(db_conn)
    assert repo.insert_if_absent(_notif()) is True


def test_insert_if_absent_returns_false_on_duplicate_key(db_conn):
    """R9 in action: replaying the same (episode_id, transition, occurrence_seq)
    is a no-op, not a crash and not a duplicate send."""
    _seed(db_conn)
    repo = SqliteNotificationsRepository(db_conn)
    repo.insert_if_absent(_notif(notif_id="n1"))
    assert repo.insert_if_absent(_notif(notif_id="n2")) is False
    assert len(repo.list()) == 1


def test_insert_if_absent_allows_different_occurrence_seq(db_conn):
    _seed(db_conn)
    repo = SqliteNotificationsRepository(db_conn)
    repo.insert_if_absent(_notif(occurrence_seq=0, transition="reminder", notif_id="n1"))
    assert repo.insert_if_absent(_notif(occurrence_seq=1, transition="reminder", notif_id="n2")) is True
    assert len(repo.list()) == 2


def test_list_filters_by_recipient_target(db_conn):
    _seed(db_conn)
    repo = SqliteNotificationsRepository(db_conn)
    repo.insert_if_absent(_notif())
    assert len(repo.list(recipient_target="lead_sam")) == 1
    assert len(repo.list(recipient_target="someone_else")) == 0


def test_list_filters_by_rule_id_and_subject_id(db_conn):
    _seed(db_conn)
    repo = SqliteNotificationsRepository(db_conn)
    repo.insert_if_absent(_notif())
    assert len(repo.list(rule_id="r1")) == 1
    assert len(repo.list(rule_id="no_such_rule")) == 0
    assert len(repo.list(subject_id="billing")) == 1
    assert len(repo.list(subject_id="tier_2")) == 0
