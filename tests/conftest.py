import pytest

from app.config import Settings
from app.storage.db import connect, migrate


@pytest.fixture
def db_conn(tmp_path):
    settings = Settings(db_path=str(tmp_path / "test.db"), schema_path="app/storage/schema.sql")
    conn = connect(settings)
    migrate(conn, settings)
    yield conn
    conn.close()
