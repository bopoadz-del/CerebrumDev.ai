"""Apply Steward Alembic migrations at runtime."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config

from app.steward.config import get_config

if TYPE_CHECKING:
    from app.steward.tenant_store import TenantStore


def run_migrations() -> None:
    """Migrate the CONTROL-PLANE database (auth tables)."""
    root = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_config().normalized_sqlalchemy_url())
    command.upgrade(cfg, "head")


def run_migrations_for(store: "TenantStore") -> None:
    """Migrate ONE tenant's SQLite file, idempotently (Phase 1.2 / T1.5).

    ``alembic upgrade head`` on a fresh file creates the schema; on an
    already-migrated file it is a no-op.
    """
    root = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", store.sqlalchemy_url)
    command.upgrade(cfg, "head")
