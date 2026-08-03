import dataclasses
import sqlite3

from app.storage.repositories import EpisodeRecord


class SqliteEpisodesRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, episode_id: str) -> EpisodeRecord | None:
        row = self._conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        return _from_row(row) if row else None

    def get_active(self, rule_id: str, subject_id: str) -> EpisodeRecord | None:
        row = self._conn.execute(
            "SELECT * FROM episodes WHERE rule_id = ? AND subject_id = ? "
            "AND state IN ('pending', 'open')",
            (rule_id, subject_id),
        ).fetchone()
        return _from_row(row) if row else None

    def create(self, record: EpisodeRecord) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        data = {**dataclasses.asdict(record), "stale": int(record.stale)}
        self._conn.execute(
            """
            INSERT INTO episodes (id, rule_id, subject_id, state, first_true_at, opened_at,
                                   closed_at, last_notified_at, notify_seq, evaluations_suppressed, stale,
                                   stale_since)
            VALUES (:id, :rule_id, :subject_id, :state, :first_true_at, :opened_at,
                    :closed_at, :last_notified_at, :notify_seq, :evaluations_suppressed, :stale,
                    :stale_since)
            """,
            data,
        )

    def update(self, record: EpisodeRecord) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        data = {**dataclasses.asdict(record), "stale": int(record.stale)}
        self._conn.execute(
            """
            UPDATE episodes SET state=:state, first_true_at=:first_true_at, opened_at=:opened_at,
                closed_at=:closed_at, last_notified_at=:last_notified_at, notify_seq=:notify_seq,
                evaluations_suppressed=:evaluations_suppressed, stale=:stale, stale_since=:stale_since
            WHERE id=:id
            """,
            data,
        )

    def list_active(self) -> list[EpisodeRecord]:
        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE state IN ('pending', 'open')"
        ).fetchall()
        return [_from_row(r) for r in rows]


def _from_row(row: sqlite3.Row) -> EpisodeRecord:
    data = {k: row[k] for k in EpisodeRecord.__dataclass_fields__}
    data["stale"] = bool(data["stale"])
    return EpisodeRecord(**data)
