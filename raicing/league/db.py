"""Database engine and session factory.

``DATABASE_URL`` selects the backend (PostgreSQL in production, e.g.
``postgresql+psycopg://user:pass@host/raicing``); without it, a SQLite file ``league.db`` is
created inside the league directory. ``postgresql://`` URLs are mapped to the psycopg 3 driver.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DEFAULT_DB_NAME = "league.db"


def default_database_url(data_dir: str | Path) -> str:
    """SQLite file inside ``data_dir``."""
    path = Path(data_dir).expanduser().resolve() / DEFAULT_DB_NAME
    return f"sqlite:///{path}"


def normalise_url(url: str) -> str:
    """Use the psycopg 3 driver for bare PostgreSQL URLs."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str | None = None, data_dir: str | Path | None = None, echo: bool = False) -> Engine:
    """Create an engine from ``url`` (or ``DATABASE_URL``, or a SQLite file in ``data_dir``)."""
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        if data_dir is None:
            raise ValueError("DATABASE_URL ou un dossier de ligue est nécessaire")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        url = default_database_url(data_dir)
    url = normalise_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False, "timeout": 60})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=60000")
            cur.close()
        return engine
    return create_engine(url, echo=echo, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    """Create every table (idempotent)."""
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


class Database:
    """Engine + session factory bundle used by the API, the worker and the local CLI."""

    def __init__(self, url: str | None = None, data_dir: str | Path | None = None, create: bool = True):
        self.engine = make_engine(url, data_dir)
        if create:
            init_db(self.engine)
        self.Session = session_factory(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.Session()
        try:
            yield s
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
