"""SQLAlchemy 2.x engine/session management for Steward production RAG.

Two stores, two boundaries (Phase 1):
- the shared CONTROL-PLANE engine (pilot tokens / principals) on the
  configured URL — auth must look up a token before any tenant exists;
- the TENANT store — one SQLite file per tenant, opened per request from
  the resolved :class:`app.steward.tenant_store.TenantStore`, migrated
  idempotently at first open, closed after the request. No engine cache,
  so nothing to bound.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.steward.config import StewardRagConfig, get_config
from app.steward.tenant_store import TenantStore

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def init_engine(config: Optional[StewardRagConfig] = None, echo: bool = False) -> Engine:
    """Control-plane engine (auth tokens / principals). Not tenant corpus."""
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
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def open_tenant_engine(store: TenantStore, echo: bool = False) -> Engine:
    """Open an engine for ONE tenant's file and migrate it at first open.

    Per request: the caller disposes it when the scope closes. A new
    tenant's first open runs ``alembic upgrade head`` idempotently;
    reopening an already-migrated file is a no-op.
    """
    store.storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        store.sqlalchemy_url,
        echo=echo,
        future=True,
    )
    from app.steward.migrations_runner import run_migrations_for

    run_migrations_for(store)
    return engine


@contextmanager
def tenant_session_scope(store: TenantStore) -> Iterator[Session]:
    """A session over the tenant's own store, opened and closed per request.

    A tenant's session can only ever see its own file: the handle is the
    boundary (T1.1 — impossible, not merely empty).
    """
    engine = open_tenant_engine(store)
    try:
        session = Session(bind=engine, expire_on_commit=False, future=True)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        engine.dispose()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Control-plane session (auth lookup) — not tenant corpus."""
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


def get_session() -> Iterator[Session]:
    """Control-plane session dependency (auth lookup) — not tenant corpus."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
