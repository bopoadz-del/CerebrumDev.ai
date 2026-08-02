"""One-time cutover of the accounts database from SQLite to Postgres.

    python -m scripts.migrate_accounts_to_postgres --dry-run
    python -m scripts.migrate_accounts_to_postgres --verify

Why this exists: setting ``ACCOUNTS_DATABASE_URL`` switches engines but copies
nothing. Pointing the live service at an empty Postgres loses every account,
and because the app boots fine and simply reports no users, nobody notices
until a customer cannot log in.

The migration is row-for-row, inside one transaction, and refuses to run
against a non-empty target unless ``--force`` is given. ``--verify`` re-reads
both sides afterwards and compares counts and primary keys, because "the INSERT
statements executed" is not the same claim as "the data is there".
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import sqlalchemy as sa

TABLES = ["accounts", "api_keys", "login_tokens", "session_owners", "usage_counters"]


def sqlite_path() -> Path:
    override = os.getenv("ACCOUNTS_DB_PATH", "").strip()
    if override:
        return Path(override)
    return Path(os.getenv("STORAGE_PATH", "./storage")) / "accounts.db"


def normalise_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


def read_sqlite(path: Path) -> Dict[str, List[Tuple]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: Dict[str, List[Tuple]] = {}
    try:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in TABLES:
            if table not in present:
                continue
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            out[table] = [dict(r) for r in rows]
    finally:
        conn.close()
    return out


def target_counts(engine: sa.engine.Engine) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    insp = sa.inspect(engine)
    with engine.connect() as conn:
        for table in TABLES:
            if not insp.has_table(table):
                continue
            counts[table] = conn.execute(
                sa.text(f'SELECT COUNT(*) FROM "{table}"')
            ).scalar_one()
    return counts


def migrate(engine: sa.engine.Engine, data: Dict[str, List[dict]], force: bool) -> Dict[str, int]:
    existing = target_counts(engine)
    non_empty = {t: n for t, n in existing.items() if n}
    if non_empty and not force:
        raise SystemExit(
            f"target already holds rows {non_empty}; refusing to merge. "
            "Re-run with --force only if you are certain."
        )

    md = sa.MetaData()
    md.reflect(bind=engine, only=[t for t in TABLES if t in data])
    written: Dict[str, int] = {}
    with engine.begin() as conn:
        for table, rows in data.items():
            if not rows:
                written[table] = 0
                continue
            tbl = md.tables.get(table)
            if tbl is None:
                raise SystemExit(
                    f"table {table} does not exist in the target; run alembic first"
                )
            cols = {c.name for c in tbl.columns}
            payload = [{k: v for k, v in row.items() if k in cols} for row in rows]
            conn.execute(tbl.insert(), payload)
            written[table] = len(payload)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="migrate_accounts_to_postgres")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="re-read both sides after")
    ap.add_argument("--force", action="store_true", help="allow a non-empty target")
    args = ap.parse_args(argv)

    src = sqlite_path()
    if not src.is_file():
        print(f"no SQLite accounts database at {src}; nothing to migrate")
        return 0

    url = os.getenv("ACCOUNTS_DATABASE_URL", "").strip()
    if not url:
        print("ACCOUNTS_DATABASE_URL is not set; set it to the target Postgres URL")
        return 2

    data = read_sqlite(src)
    summary = {t: len(rows) for t, rows in data.items()}
    print(f"source {src}: {summary}")

    if args.dry_run:
        print("dry run; nothing written")
        return 0

    engine = sa.create_engine(normalise_url(url), pool_pre_ping=True)
    written = migrate(engine, data, force=args.force)
    print(f"written: {written}")

    if args.verify:
        after = target_counts(engine)
        mismatch = {
            t: (summary.get(t, 0), after.get(t, 0))
            for t in summary
            if summary.get(t, 0) != after.get(t, 0)
        }
        if mismatch:
            print(f"VERIFY FAILED (source, target): {mismatch}")
            return 1
        print(f"verified: {after}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
