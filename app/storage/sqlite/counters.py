import sqlite3


class SqliteCountersRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def increment(self, name: str, by: int = 1) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        self._conn.execute(
            "INSERT INTO ingest_counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
            (name, by),
        )

    def get(self, name: str) -> int:
        row = self._conn.execute(
            "SELECT value FROM ingest_counters WHERE name = ?", (name,)
        ).fetchone()
        return row["value"] if row else 0

    def all(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT name, value FROM ingest_counters").fetchall()
        return {row["name"]: row["value"] for row in rows}
