from datetime import datetime, timezone

from app.domain.episodes import Transition
from app.domain.notifications import Notification
from app.routing.dispatcher import dispatch
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.notifications import SqliteNotificationsRepository
from app.storage.sqlite.rules import SqliteRulesRepository
from app.storage.repositories import EpisodeRecord, RuleRecord


class _SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def _notification(**overrides) -> Notification:
    data = dict(
        id="n1", episode_id="ep1", rule_id="r1", subject_id="billing",
        transition=Transition.OPENED, occurrence_seq=0,
        recipient_kind="author", recipient_target="lead_sam",
        body="billing has 22 tickets waiting", facts_snapshot={"tickets_waiting": 22},
        event_time=datetime(2026, 5, 26, 9, 36, tzinfo=timezone.utc),
        created_at=datetime(2026, 5, 26, 9, 36, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return Notification(**data)


def _seed(db_conn):
    SqliteRulesRepository(db_conn).create(
        RuleRecord("r1", "n", "queue", "{}", "[]", 0, 0, "author", None, "t", True,
                    "lead_sam", "2026-05-26T09:00:00Z", 1)
    )
    SqliteEpisodesRepository(db_conn).create(
        EpisodeRecord("ep1", "r1", "billing", "open", None, "2026-05-26T09:36:00Z", None, None, 0, 0, False)
    )


def test_dispatch_persists_and_sends_to_all_channels(db_conn):
    _seed(db_conn)
    channel_a, channel_b = _SpyChannel(), _SpyChannel()
    notifications_repo = SqliteNotificationsRepository(db_conn)

    dispatch(_notification(), [channel_a, channel_b], db_conn, notifications_repo)

    assert len(notifications_repo.list()) == 1
    assert len(channel_a.sent) == 1
    assert len(channel_b.sent) == 1


def test_dispatch_does_not_resend_on_duplicate_insert(db_conn):
    """Phase 6 item 1: persist-then-send, gated on rowcount. A duplicate
    (episode_id, transition, occurrence_seq) -- e.g. replaying the same
    events twice -- must not reach Channel.send() a second time: a
    duplicate console line on recovery is a visible bug (R9/R10)."""
    _seed(db_conn)
    channel = _SpyChannel()
    notifications_repo = SqliteNotificationsRepository(db_conn)

    dispatch(_notification(), [channel], db_conn, notifications_repo)
    dispatch(_notification(), [channel], db_conn, notifications_repo)

    assert len(notifications_repo.list()) == 1
    assert len(channel.sent) == 1


def test_dispatch_persists_before_reconnect_is_visible():
    """Persist-then-send: the DB write must actually be committed, visible
    from a fresh connection, not left open until some later call."""
    import tempfile

    from app.config import Settings
    from app.storage.db import connect, migrate

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(db_path=f"{tmp}/t.db", schema_path="app/storage/schema.sql")
        conn = connect(settings)
        migrate(conn, settings)
        _seed(conn)
        dispatch(_notification(), [_SpyChannel()], conn, SqliteNotificationsRepository(conn))
        conn.close()

        reconnected = connect(settings)
        assert len(SqliteNotificationsRepository(reconnected).list()) == 1
        reconnected.close()
