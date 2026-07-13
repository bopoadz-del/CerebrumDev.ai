"""SQLAlchemy 2.x engine/session management for the RetailOps runtime."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.retailops.config import RetailOpsConfig, get_config

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def init_engine(config: Optional[RetailOpsConfig] = None, echo: bool = False) -> Engine:
    """Create (or return) the process-wide engine."""
    global _engine, _SessionLocal
    config = config or get_config()
    if _engine is None:
        _engine = create_engine(
            config.normalized_sqlalchemy_url(),
            echo=echo,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


def reset_engine() -> None:
    """Dispose the engine (used by tests to rebind to a different database)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope with commit/rollback."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    """FastAPI-style dependency: yields a session and closes it."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
