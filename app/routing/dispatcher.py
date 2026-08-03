import json
import sqlite3

from app.domain.facts import MISSING
from app.domain.notifications import Notification
from app.routing.channels import Channel
from app.storage.db import transaction
from app.storage.repositories import NotificationRecord, NotificationsRepository


def _to_record(notification: Notification) -> NotificationRecord:
    facts_json = {
        k: ("MISSING" if v is MISSING else v) for k, v in notification.facts_snapshot.items()
    }
    return NotificationRecord(
        id=notification.id,
        episode_id=notification.episode_id,
        rule_id=notification.rule_id,
        subject_id=notification.subject_id,
        transition=notification.transition.value,
        occurrence_seq=notification.occurrence_seq,
        recipient_kind=notification.recipient_kind,
        recipient_target=notification.recipient_target,
        body=notification.body,
        facts_snapshot_json=json.dumps(facts_json),
        event_time=notification.event_time.isoformat(),
        created_at=notification.created_at.isoformat(),
    )


def dispatch_persist(notification: Notification, notifications_repo: NotificationsRepository) -> bool:
    """The DB-write half of persist-then-send. Does not commit -- callers
    wrap this in their own transaction(), typically alongside the episode
    write it belongs with (code-review HIGH fix: a notification must not be
    committed in a transaction separate from the episode state change that
    produced it, or a crash between the two commits permanently loses the
    notification while the episode has already moved on). Returns True if a
    new row was actually inserted (Phase 6 gates Channel.send() on this)."""
    record = _to_record(notification)
    return notifications_repo.insert_if_absent(record)


def dispatch_send(notification: Notification, channels: list[Channel]) -> None:
    """The send half. Call only AFTER the persisting transaction commits."""
    for channel in channels:
        channel.send(notification)


def dispatch(
    notification: Notification,
    channels: list[Channel],
    conn: sqlite3.Connection,
    notifications_repo: NotificationsRepository,
) -> None:
    """Convenience wrapper for dispatching a single, standalone notification
    (its own transaction). Persist-then-send: the DB write commits before
    any Channel.send() runs, and send only happens when a row was actually
    inserted (R9/R10) -- a duplicate (episode_id, transition,
    occurrence_seq), e.g. from replaying the same events twice, must not
    reach the channel a second time."""
    with transaction(conn):
        inserted = dispatch_persist(notification, notifications_repo)
    if inserted:
        dispatch_send(notification, channels)
