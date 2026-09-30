"""SQLite engine, migrations and controlled write transactions."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from .config import paths

log = logging.getLogger(__name__)

_engine: Engine | None = None
_Session: sessionmaker | None = None
# SQLite allows one writer at a time. Serialising writes in-process avoids
# "database is locked" errors between the API, capture and worker threads.
_write_lock = threading.RLock()

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        url = f"sqlite:///{paths().db_file.as_posix()}"
        _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})
        event.listen(_engine, "connect", _sqlite_pragmas)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Dispose the engine (tests / data-dir switch)."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{paths().db_file.as_posix()}")
    eng = engine()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    log.info("Database migrated: %s", paths().db_file)


@contextmanager
def read_session() -> Iterator[OrmSession]:
    engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
    finally:
        s.close()


@contextmanager
def write_session() -> Iterator[OrmSession]:
    """A serialised transaction: commits on success, rolls back on any error."""
    engine()
    assert _Session is not None
    with _write_lock:
        s = _Session()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()
