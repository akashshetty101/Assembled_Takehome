import json
import sqlite3
import uuid
from pathlib import Path

from app.clock import SystemClock
from app.config import Settings
from app.domain.rules import Rule
from app.evaluation.registry import build_registry
from app.storage.db import transaction
from app.storage.repositories import RuleRecord
from app.storage.sqlite.rules import SqliteRulesRepository


def seed_rules(conn: sqlite3.Connection, settings: Settings) -> int:
    """Loads seeds_path through the exact same Rule.model_validate path the
    API will use in Phase 5's routes_rules.py -- no back door, no
    special-cased parsing for "seed data" versus "API-submitted data"."""
    registry = build_registry()
    raw_rules = json.loads(Path(settings.seeds_path).read_text())
    rules = [Rule.model_validate(raw, context={"registry": registry}) for raw in raw_rules]

    created_at = SystemClock().now().isoformat()
    repo = SqliteRulesRepository(conn)
    with transaction(conn):
        for rule in rules:
            repo.create(RuleRecord(
                id=str(uuid.uuid4()), name=rule.name, subject_type=rule.subject_type.value,
                selector_json=rule.selector.model_dump_json(),
                conditions_json=json.dumps([c.model_dump(mode="json") for c in rule.conditions]),
                for_duration_sec=rule.for_duration_sec, cooldown_sec=rule.cooldown_sec,
                recipient_kind=rule.recipient.kind.value, recipient_target=rule.recipient.target,
                template=rule.template, enabled=rule.enabled, created_by=rule.created_by,
                created_at=created_at, version=1,
            ))
    return len(rules)


def main() -> None:
    from app.storage.db import connect, migrate

    settings = Settings()
    conn = connect(settings)
    migrate(conn, settings)
    count = seed_rules(conn, settings)
    conn.close()
    print(f"Seeded {count} rules from {settings.seeds_path}")


if __name__ == "__main__":
    main()
