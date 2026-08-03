import dataclasses
import sqlite3

from app.storage.repositories import NotificationRecord


class SqliteNotificationsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert_if_absent(self, record: NotificationRecord) -> bool:
        """Does not commit; wrap in storage.db.transaction(). Phase 6 calls
        this inside the same transaction as the episode update it follows
        (persist-then-send), then calls Channel.send() only if this returns
        True -- rowcount reflects the INSERT regardless of commit timing."""
        cursor = self._conn.execute(
            """
            INSERT INTO notifications (id, episode_id, rule_id, subject_id, transition,
                                        occurrence_seq, recipient_kind, recipient_target, body,
                                        facts_snapshot_json, event_time, created_at)
            VALUES (:id, :episode_id, :rule_id, :subject_id, :transition, :occurrence_seq,
                    :recipient_kind, :recipient_target, :body, :facts_snapshot_json,
                    :event_time, :created_at)
            ON CONFLICT(episode_id, transition, occurrence_seq) DO NOTHING
            """,
            dataclasses.asdict(record),
        )
        return cursor.rowcount > 0

    def list(
        self,
        *,
        recipient_target: str | None = None,
        rule_id: str | None = None,
        subject_id: str | None = None,
    ) -> list[NotificationRecord]:
        query = "SELECT * FROM notifications WHERE 1=1"
        params: dict = {}
        if recipient_target is not None:
            query += " AND recipient_target = :recipient_target"
            params["recipient_target"] = recipient_target
        if rule_id is not None:
            query += " AND rule_id = :rule_id"
            params["rule_id"] = rule_id
        if subject_id is not None:
            query += " AND subject_id = :subject_id"
            params["subject_id"] = subject_id
        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [_from_row(r) for r in rows]


def _from_row(row: sqlite3.Row) -> NotificationRecord:
    return NotificationRecord(**{k: row[k] for k in NotificationRecord.__dataclass_fields__})
