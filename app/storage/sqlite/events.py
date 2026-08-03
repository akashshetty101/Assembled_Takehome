import sqlite3

from app.storage.repositories import EventRecord


class SqliteEventsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def exists(self, event_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None

    def get(self, event_id: str) -> EventRecord | None:
        row = self._conn.execute(
            "SELECT event_id, ts, type, payload_json, received_seq "
            "FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return _from_row(row) if row else None

    def append(self, record: EventRecord) -> None:
        """Raises sqlite3.IntegrityError on a duplicate event_id by design --
        dedup is the caller's responsibility (check exists() first; see
        ingest/pipeline.py's dedup-before-watermark gate ordering, R3).
        Does not commit; wrap in storage.db.transaction()."""
        self._conn.execute(
            "INSERT INTO events (event_id, ts, type, payload_json, received_seq) "
            "VALUES (?, ?, ?, ?, ?)",
            (record.event_id, record.ts, record.type, record.payload_json, record.received_seq),
        )


def _from_row(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"],
        ts=row["ts"],
        type=row["type"],
        payload_json=row["payload_json"],
        received_seq=row["received_seq"],
    )
