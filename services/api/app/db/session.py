"""SQLAlchemy engine / session plumbing.

PostgreSQL is the recommended store. SQLite is an accepted *fallback* that
the user opts into via ``DATABASE_URL``; it is never silently chosen.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _build_engine() -> tuple[Engine, sessionmaker[Session]]:
    settings = get_settings()
    url = settings.database_url
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            future=True,
        )
    else:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, factory


def init_engine() -> None:
    """Create the engine/session factory. Idempotent."""
    global _engine, _session_factory
    if _engine is None:
        _engine, _session_factory = _build_engine()


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope around a series of operations."""
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


def dispose_engine() -> None:
    """Test helper."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def check_db_connection() -> tuple[bool, str]:
    """Return (ok, detail) for a `SELECT 1` health probe."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "connected"
    except Exception as exc:  # pragma: no cover - surfaced through /health
        return False, f"{type(exc).__name__}: {exc}"
