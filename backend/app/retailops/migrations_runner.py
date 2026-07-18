"""Programmatic Alembic runner used by startup and tests."""

from __future__ import annotations

import os
from typing import Optional

from alembic import command
from alembic.config import Config

_HERE = os.path.dirname(os.path.abspath(__file__))


def make_config(database_url: Optional[str] = None) -> Config:
    cfg = Config(os.path.join(_HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_HERE, "migrations"))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_to_head(database_url: Optional[str] = None) -> None:
    command.upgrade(make_config(database_url), "head")


def downgrade_to_base(database_url: Optional[str] = None) -> None:
    command.downgrade(make_config(database_url), "base")
