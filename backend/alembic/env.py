"""Alembic environment — accounts DB.

The URL mirrors ``app.core.accounts_store._database_url``:
- ``ACCOUNTS_DATABASE_URL`` (Postgres; ``postgres://`` is upgraded to the
  psycopg driver) when set,
- otherwise sqlite at ``ACCOUNTS_DB_PATH`` or ``STORAGE_PATH/accounts.db``.

The schema itself is imported from the application at migration time, so the
migration can never drift from the code that writes the data.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app` importable whether alembic runs from the repo (backend/) or the
# production image (/app).
_HERE = Path(__file__).resolve()
for candidate in (_HERE.parents[1], _HERE.parents[2]):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    url = os.getenv("ACCOUNTS_DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        return url
    db_path = os.getenv("ACCOUNTS_DB_PATH", "").strip()
    if not db_path:
        storage = os.getenv("STORAGE_PATH", "./storage")
        db_path = str(Path(storage) / "accounts.db")
    return f"sqlite:///{db_path}"


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
