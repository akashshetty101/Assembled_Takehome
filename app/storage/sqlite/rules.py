import dataclasses
import sqlite3

from app.storage.repositories import RuleRecord


class SqliteRulesRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, rule_id: str) -> RuleRecord | None:
        row = self._conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return _from_row(row) if row else None

    def list(self, *, enabled_only: bool = False) -> list[RuleRecord]:
        query = "SELECT * FROM rules"
        if enabled_only:
            query += " WHERE enabled = 1"
        rows = self._conn.execute(query).fetchall()
        return [_from_row(r) for r in rows]

    def create(self, record: RuleRecord) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        data = {**dataclasses.asdict(record), "enabled": int(record.enabled)}
        self._conn.execute(
            """
            INSERT INTO rules (id, name, subject_type, selector_json, conditions_json,
                                for_duration_sec, cooldown_sec, recipient_kind, recipient_target,
                                template, enabled, created_by, created_at, version)
            VALUES (:id, :name, :subject_type, :selector_json, :conditions_json,
                    :for_duration_sec, :cooldown_sec, :recipient_kind, :recipient_target,
                    :template, :enabled, :created_by, :created_at, :version)
            """,
            data,
        )

    def update(self, record: RuleRecord) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        data = {**dataclasses.asdict(record), "enabled": int(record.enabled)}
        self._conn.execute(
            """
            UPDATE rules SET name=:name, subject_type=:subject_type, selector_json=:selector_json,
                conditions_json=:conditions_json, for_duration_sec=:for_duration_sec,
                cooldown_sec=:cooldown_sec, recipient_kind=:recipient_kind,
                recipient_target=:recipient_target, template=:template, enabled=:enabled,
                created_by=:created_by, created_at=:created_at, version=:version
            WHERE id=:id
            """,
            data,
        )


def _from_row(row: sqlite3.Row) -> RuleRecord:
    data = {k: row[k] for k in RuleRecord.__dataclass_fields__}
    data["enabled"] = bool(data["enabled"])
    return RuleRecord(**data)
