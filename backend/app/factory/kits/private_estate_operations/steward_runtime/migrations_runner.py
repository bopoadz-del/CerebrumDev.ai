"""Apply Steward Alembic migrations at runtime."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.steward.config import get_config


def run_migrations() -> None:
    root = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_config().normalized_sqlalchemy_url())
    command.upgrade(cfg, "head")
