import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import Settings


def connect(settings: Settings) -> sqlite3.Connection:
    """Connection factory. The only place PRAGMAs are set.

    check_same_thread=False: FastAPI (Phase 5) runs synchronous route
    handlers in a worker thread pool, not the thread that opened this
    connection. This is safe here specifically because R12 already commits
    the whole system to single-threaded, non-concurrent DB access -- the
    partial unique index on open episodes is the enforced backstop for that
    invariant, not this flag. This flag only relaxes sqlite3's same-OS-
    thread-object check; it does not introduce concurrent writes."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection, settings: Settings) -> None:
    """Idempotent: schema.sql is entirely CREATE ... IF NOT EXISTS.
    executescript() commits any pending transaction before running, and DDL
    is not part of sqlite3's implicit transaction handling, so no trailing
    commit() is needed here."""
    schema_path = Path(settings.schema_path)
    conn.executescript(schema_path.read_text())


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """The only place repository writes are committed. Repository methods in
    storage/sqlite/*.py execute() but never commit()/rollback() themselves --
    callers group one or more repository calls into a single atomic unit of
    work with this context manager. This is what makes R4's requirement
    satisfiable: "the watermark must advance only from accepted events, in
    the same transaction as the projection write, or a crash mid-batch
    permanently drops a subject's future events."

    Usage:
        with transaction(conn):
            events_repo.append(record)
            queue_repo.put(new_state)
    """
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
