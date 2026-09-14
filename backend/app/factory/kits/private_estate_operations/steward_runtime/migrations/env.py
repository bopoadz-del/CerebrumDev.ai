"""Alembic env for Steward production RAG migrations."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.steward.config import get_config
from app.steward.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """The migration target URL.

    Prefers an explicit ``sqlalchemy.url`` set on the alembic Config — that
    is how the Phase 1 seam migrates ONE tenant's SQLite file
    (``run_migrations_for``). Falls back to the configured control-plane
    URL for the legacy single-database path.
    """
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    return get_config().normalized_sqlalchemy_url()


def run_migrations_offline() -> None:
    url = _url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
