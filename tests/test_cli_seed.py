from pathlib import Path

from app.config import Settings
from app.storage.db import connect, migrate


def test_seed_loads_through_the_same_validation_as_the_api(tmp_path):
    """No back door: cli/seed.py validates through Rule.model_validate the
    exact same way an API request body would."""
    from app.cli.seed import seed_rules

    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql",
                         seeds_path="seeds/rules.json")
    conn = connect(settings)
    migrate(conn, settings)
    count = seed_rules(conn, settings)
    conn.close()
    assert count == 7


def test_seed_persists_visibly_after_reconnect(tmp_path):
    from app.cli.seed import seed_rules
    from app.storage.sqlite.rules import SqliteRulesRepository

    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql",
                         seeds_path="seeds/rules.json")
    conn = connect(settings)
    migrate(conn, settings)
    seed_rules(conn, settings)
    conn.close()

    reconnected = connect(settings)
    rules = SqliteRulesRepository(reconnected).list()
    reconnected.close()
    assert len(rules) == 7
    assert all(r.enabled for r in rules)


def test_seed_rejects_an_invalid_rule_file(tmp_path):
    """A malformed rules file must fail the same way a bad API POST would --
    loudly, via ValidationError, not silently skipped."""
    import json

    from pydantic import ValidationError

    from app.cli.seed import seed_rules

    bad_path = tmp_path / "bad_rules.json"
    bad_path.write_text(json.dumps([{"name": "n", "subject_type": "queue"}]))  # missing required fields
    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql",
                         seeds_path=str(bad_path))
    conn = connect(settings)
    migrate(conn, settings)
    try:
        import pytest
        with pytest.raises(ValidationError):
            seed_rules(conn, settings)
    finally:
        conn.close()
