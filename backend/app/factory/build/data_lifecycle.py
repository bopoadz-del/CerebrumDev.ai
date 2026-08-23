"""S10 data lifecycle emitters for RoleRunner products.

Unused kits already carry Alembic (steward_runtime/migrations). RoleRunner
did not emit it; generated products used ``CREATE TABLE IF NOT EXISTS`` on
every ``store.connect()``. This module is the WRITER/TESTER emission for
versioned up/down migrations, WAL durability, backup/restore/retention, and
the product-side tests that *perform* a restore drill.

SQLite on a single Render disk is retained. That is a SPOF. Capacity is the
disk size (1 GiB in the emitted render.yaml). Backups on the same volume do
not survive disk loss; set BACKUP_DIR onto another volume if that is in scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

# anyio / Starlette default limiter for FastAPI sync def endpoints.
FASTAPI_SYNC_THREADPOOL = 40
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_CONNECT_TIMEOUT_S = 30.0
BACKUP_KEEP = 14
DISK_SIZE_GB = 1
REVISION_0001 = "0001_baseline"
REVISION_0002 = "0002_lifecycle_audit"
AUDIT_TABLE = "lifecycle_audit"

_SA_TYPES = {
    "str": "sa.Text()",
    "int": "sa.Integer()",
    "float": "sa.Float()",
    "bool": "sa.Integer()",
}


def table_specs(specs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for spec in sorted(specs.values(), key=lambda s: s["entity"]):
        fields = list(spec.get("fields") or [])
        out.append({"entity": spec["entity"], "fields": fields})
    return out


def columns_map(specs: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    return {
        spec["entity"]: [f["name"] for f in spec.get("fields") or []]
        for spec in specs.values()
    }


def first_entity_sample(specs: Dict[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """A deterministic row the generated lifecycle tests can insert."""
    if not specs:
        return "", {}
    spec = table_specs(specs)[0]
    sample: Dict[str, Any] = {}
    for field in spec["fields"]:
        name = field["name"]
        ftype = field.get("type") or "str"
        if field.get("allowed_values"):
            sample[name] = field["allowed_values"][0]
        elif ftype == "int":
            sample[name] = int(field["min"]) if field.get("min") is not None else 1
        elif ftype == "float":
            sample[name] = float(field["min"]) if field.get("min") is not None else 1.5
        elif ftype == "bool":
            sample[name] = True
        else:
            sample[name] = "s10-row"
    return spec["entity"], sample


def render_store(specs: Dict[str, Dict[str, Any]]) -> str:
    """stdlib sqlite3 persistence. Schema comes from Alembic, not connect()."""
    columns = columns_map(specs)
    tables = tuple(sorted(columns))
    return (
        '"""SQLite persistence for the domain models.\n'
        "\n"
        "stdlib sqlite3 and a local file. Schema is applied by Alembic\n"
        "(app/migrations.py + alembic/versions/), never by this module.\n"
        "CREATE TABLE IF NOT EXISTS is forbidden here: a missing revision is\n"
        "a deploy failure, not a silent create.\n"
        "\n"
        "Durability: WAL + busy_timeout matched to the FastAPI sync\n"
        f"threadpool ({FASTAPI_SYNC_THREADPOOL} workers). One writer; readers\n"
        "proceed. STORAGE_PATH relocates the file onto the mounted disk.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import sqlite3\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, List, Tuple\n"
        "\n"
        f"TABLES: Tuple[str, ...] = {tables!r}\n"
        f"COLUMNS: Dict[str, List[str]] = {columns!r}\n"
        f"SQLITE_BUSY_TIMEOUT_MS = {SQLITE_BUSY_TIMEOUT_MS}\n"
        f"SQLITE_CONNECT_TIMEOUT_S = {SQLITE_CONNECT_TIMEOUT_S}\n"
        f"FASTAPI_SYNC_THREADPOOL = {FASTAPI_SYNC_THREADPOOL}\n"
        "\n"
        "\n"
        "def db_path() -> Path:\n"
        '    root = Path(os.getenv("STORAGE_PATH", "./data"))\n'
        "    root.mkdir(parents=True, exist_ok=True)\n"
        '    return root / "platform.db"\n'
        "\n"
        "\n"
        "def connect() -> sqlite3.Connection:\n"
        '    """Open the file. Does not create domain tables."""\n'
        "    conn = sqlite3.connect(\n"
        "        str(db_path()),\n"
        "        timeout=SQLITE_CONNECT_TIMEOUT_S,\n"
        "        check_same_thread=False,\n"
        "        isolation_level=\"DEFERRED\",\n"
        "    )\n"
        "    conn.row_factory = sqlite3.Row\n"
        "    conn.execute(\"PRAGMA journal_mode=WAL\")\n"
        "    conn.execute(f\"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}\")\n"
        "    conn.execute(\"PRAGMA synchronous=NORMAL\")\n"
        "    conn.execute(\"PRAGMA foreign_keys=ON\")\n"
        "    return conn\n"
        "\n"
        "\n"
        "def save(entity: str, record: Dict[str, Any]) -> Dict[str, Any]:\n"
        '    """Insert a record and return it with its assigned id."""\n'
        "    cols = COLUMNS[entity]\n"
        "    values = [record.get(c) for c in cols]\n"
        '    placeholders = ", ".join("?" for _ in cols)\n'
        "    conn = connect()\n"
        "    try:\n"
        "        cur = conn.execute(\n"
        '            f"INSERT INTO {entity} ({\', \'.join(cols)}) VALUES ({placeholders})",\n'
        "            values,\n"
        "        )\n"
        "        conn.commit()\n"
        '        return {"id": cur.lastrowid, **{c: record.get(c) for c in cols}}\n'
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def list_all(entity: str) -> List[Dict[str, Any]]:\n"
        "    conn = connect()\n"
        "    try:\n"
        '        rows = conn.execute(f"SELECT * FROM {entity} ORDER BY id").fetchall()\n'
        "        return [dict(r) for r in rows]\n"
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def get(entity: str, record_id: int) -> Dict[str, Any] | None:\n"
        "    conn = connect()\n"
        "    try:\n"
        "        row = conn.execute(\n"
        '            f"SELECT * FROM {entity} WHERE id = ?", (record_id,)\n'
        "        ).fetchone()\n"
        "        return dict(row) if row else None\n"
        "    finally:\n"
        "        conn.close()\n"
    )


def render_migrations() -> str:
    return (
        '"""Apply and roll back Alembic revisions for this platform.\n'
        "\n"
        "Deploy (scripts/entrypoint.sh) and FastAPI lifespan both call\n"
        "upgrade_head() against STORAGE_PATH. Failure refuses boot.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import sqlite3\n"
        "from pathlib import Path\n"
        "\n"
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "\n"
        "from app.store import connect, db_path\n"
        "\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "\n"
        "\n"
        "def alembic_config() -> Config:\n"
        '    cfg = Config(str(ROOT / "alembic.ini"))\n'
        '    cfg.set_main_option("script_location", str(ROOT / "alembic"))\n'
        "    return cfg\n"
        "\n"
        "\n"
        "def upgrade_head() -> str | None:\n"
        '    command.upgrade(alembic_config(), "head")\n'
        "    return current_revision()\n"
        "\n"
        "\n"
        "def upgrade_to(revision: str) -> str | None:\n"
        "    command.upgrade(alembic_config(), revision)\n"
        "    return current_revision()\n"
        "\n"
        "\n"
        "def downgrade(revision: str) -> str | None:\n"
        "    command.downgrade(alembic_config(), revision)\n"
        "    return current_revision()\n"
        "\n"
        "\n"
        "def current_revision() -> str | None:\n"
        "    if not db_path().exists():\n"
        "        return None\n"
        "    conn = connect()\n"
        "    try:\n"
        "        row = conn.execute(\n"
        '            "SELECT version_num FROM alembic_version"\n'
        "        ).fetchone()\n"
        "        return str(row[0]) if row else None\n"
        "    except sqlite3.OperationalError:\n"
        "        return None\n"
        "    finally:\n"
        "        conn.close()\n"
    )


def render_backup() -> str:
    return (
        '"""Backup, restore, and retention for platform.db.\n'
        "\n"
        "Uses SQLite's online backup API (not a file copy) so a live WAL\n"
        "writer cannot produce a torn snapshot. A restore that has not been\n"
        "drilled is not a restore — tests/test_data_lifecycle.py performs\n"
        "backup → wipe → restore → assert rows.\n"
        "\n"
        "Same-disk BACKUP_DIR (the default) protects against logical loss,\n"
        "not disk loss. The mounted Render disk is a SPOF.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import sqlite3\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from typing import List\n"
        "\n"
        "from app.store import db_path\n"
        "\n"
        f"DEFAULT_KEEP = {BACKUP_KEEP}\n"
        "\n"
        "\n"
        "def _utcstamp() -> str:\n"
        '    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")\n'
        "\n"
        "\n"
        "def backup_root() -> Path:\n"
        '    override = os.getenv("BACKUP_DIR", "").strip()\n'
        "    if override:\n"
        "        return Path(override)\n"
        '    return Path(os.getenv("STORAGE_PATH", "./data")) / "backups"\n'
        "\n"
        "\n"
        "def _sidecar_paths(db: Path) -> List[Path]:\n"
        '    return [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]\n'
        "\n"
        "\n"
        "def create_backup() -> Path:\n"
        "    src = db_path()\n"
        "    dest_dir = backup_root()\n"
        "    dest_dir.mkdir(parents=True, exist_ok=True)\n"
        '    dest = dest_dir / f"platform-{_utcstamp()}.db"\n'
        "    src_conn = sqlite3.connect(str(src))\n"
        "    dest_conn = sqlite3.connect(str(dest))\n"
        "    try:\n"
        "        src_conn.backup(dest_conn)\n"
        "    finally:\n"
        "        dest_conn.close()\n"
        "        src_conn.close()\n"
        "    prune_backups(keep=DEFAULT_KEEP)\n"
        "    return dest\n"
        "\n"
        "\n"
        "def restore_backup(archive: Path, dest: Path | None = None) -> Path:\n"
        "    dest = dest or db_path()\n"
        "    dest.parent.mkdir(parents=True, exist_ok=True)\n"
        "    if dest.exists():\n"
        "        dest.unlink()\n"
        "    for side in _sidecar_paths(dest):\n"
        "        if side.exists():\n"
        "            side.unlink()\n"
        "    src_conn = sqlite3.connect(str(archive))\n"
        "    dest_conn = sqlite3.connect(str(dest))\n"
        "    try:\n"
        "        src_conn.backup(dest_conn)\n"
        "    finally:\n"
        "        dest_conn.close()\n"
        "        src_conn.close()\n"
        "    return dest\n"
        "\n"
        "\n"
        "def wipe_database() -> None:\n"
        "    target = db_path()\n"
        "    if target.exists():\n"
        "        target.unlink()\n"
        "    for side in _sidecar_paths(target):\n"
        "        if side.exists():\n"
        "            side.unlink()\n"
        "\n"
        "\n"
        "def prune_backups(keep: int = DEFAULT_KEEP) -> List[Path]:\n"
        "    root = backup_root()\n"
        "    if not root.is_dir():\n"
        "        return []\n"
        '    archives = sorted(root.glob("platform-*.db"))\n'
        "    removed: List[Path] = []\n"
        "    for stale in archives[: max(0, len(archives) - keep)]:\n"
        "        stale.unlink()\n"
        "        removed.append(stale)\n"
        "    return removed\n"
        "\n"
        "\n"
        "def list_backups() -> List[Path]:\n"
        "    root = backup_root()\n"
        "    if not root.is_dir():\n"
        "        return []\n"
        '    return sorted(root.glob("platform-*.db"))\n'
    )


def render_alembic_ini() -> str:
    return (
        "[alembic]\n"
        "script_location = alembic\n"
        "prepend_sys_path = .\n"
        "\n"
        "[loggers]\n"
        "keys = root,sqlalchemy,alembic\n"
        "\n"
        "[handlers]\n"
        "keys = console\n"
        "\n"
        "[formatters]\n"
        "keys = generic\n"
        "\n"
        "[logger_root]\n"
        "level = WARN\n"
        "handlers = console\n"
        "\n"
        "[logger_sqlalchemy]\n"
        "level = WARN\n"
        "handlers =\n"
        "qualname = sqlalchemy.engine\n"
        "\n"
        "[logger_alembic]\n"
        "level = INFO\n"
        "handlers =\n"
        "qualname = alembic\n"
        "\n"
        "[handler_console]\n"
        "class = StreamHandler\n"
        "args = (sys.stderr,)\n"
        "level = NOTSET\n"
        "formatter = generic\n"
        "\n"
        "[formatter_generic]\n"
        "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
    )


def render_alembic_env() -> str:
    return (
        '"""Alembic env for a generated platform (SQLite on STORAGE_PATH)."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "from logging.config import fileConfig\n"
        "from pathlib import Path\n"
        "\n"
        "from alembic import context\n"
        "from sqlalchemy import create_engine, pool\n"
        "\n"
        "config = context.config\n"
        "if config.config_file_name is not None:\n"
        "    fileConfig(config.config_file_name)\n"
        "\n"
        "\n"
        "def sqlalchemy_url() -> str:\n"
        "    # Must match app.store.db_path(). Duplicated so a migration can\n"
        "    # run before app is imported.\n"
        '    root = Path(os.getenv("STORAGE_PATH", "./data"))\n'
        "    root.mkdir(parents=True, exist_ok=True)\n"
        '    return "sqlite:///" + (root / "platform.db").resolve().as_posix()\n'
        "\n"
        "\n"
        "def run_migrations_offline() -> None:\n"
        "    context.configure(\n"
        "        url=sqlalchemy_url(),\n"
        "        literal_binds=True,\n"
        '        dialect_opts={"paramstyle": "named"},\n'
        "    )\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
        "\n"
        "\n"
        "def run_migrations_online() -> None:\n"
        "    connectable = create_engine(sqlalchemy_url(), poolclass=pool.NullPool)\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(connection=connection)\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n"
        "\n"
        "\n"
        "if context.is_offline_mode():\n"
        "    run_migrations_offline()\n"
        "else:\n"
        "    run_migrations_online()\n"
    )


def render_script_mako() -> str:
    return (
        '"""${message}\n'
        "\n"
        "Revision ID: ${up_revision}\n"
        "Revises: ${down_revision | comma,n}\n"
        "Create Date: ${create_date}\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "${imports if imports else ''}\n"
        "\n"
        "revision = ${repr(up_revision)}\n"
        "down_revision = ${repr(down_revision)}\n"
        "branch_labels = ${repr(branch_labels)}\n"
        "depends_on = ${repr(depends_on)}\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    ${upgrades if upgrades else 'pass'}\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        "    ${downgrades if downgrades else 'pass'}\n"
    )


def render_revision_0001(specs: Dict[str, Dict[str, Any]]) -> str:
    tables = table_specs(specs)
    upgrade_lines: List[str] = []
    downgrade_lines: List[str] = []
    for spec in tables:
        cols = [
            '        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),'
        ]
        for field in spec["fields"]:
            sa_type = _SA_TYPES.get(field.get("type") or "str", "sa.Text()")
            cols.append(f'        sa.Column("{field["name"]}", {sa_type}, nullable=True),')
        upgrade_lines.append(f'    op.create_table(\n        "{spec["entity"]}",')
        upgrade_lines.extend(cols)
        upgrade_lines.append("    )")
        downgrade_lines.append(f'    op.drop_table("{spec["entity"]}")')
    if not upgrade_lines:
        upgrade_lines = ["    pass"]
        downgrade_lines = ["    pass"]
    else:
        downgrade_lines = list(reversed(downgrade_lines))
    return (
        '"""v1 domain tables from the capability specs.\n'
        "\n"
        f"Revision ID: {REVISION_0001}\n"
        "Revises:\n"
        "Create Date: 2026-08-23\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "\n"
        f'revision = "{REVISION_0001}"\n'
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        + "\n".join(upgrade_lines)
        + "\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        + "\n".join(downgrade_lines)
        + "\n"
    )


def render_revision_0002() -> str:
    return (
        '"""v2 schema change: lifecycle_audit table (up and down).\n'
        "\n"
        f"Revision ID: {REVISION_0002}\n"
        f"Revises: {REVISION_0001}\n"
        "Create Date: 2026-08-23\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "\n"
        f'revision = "{REVISION_0002}"\n'
        f'down_revision = "{REVISION_0001}"\n'
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    op.create_table(\n"
        f'        "{AUDIT_TABLE}",\n'
        '        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),\n'
        '        sa.Column("event", sa.Text(), nullable=False),\n'
        '        sa.Column("at", sa.Text(), nullable=False),\n'
        "    )\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        f'    op.drop_table("{AUDIT_TABLE}")\n'
    )


def render_entrypoint() -> str:
    return (
        "#!/bin/sh\n"
        "# Apply versioned migrations against the persistent disk, then serve.\n"
        "# Failure here refuses boot (fail-closed). Do not start uvicorn on a\n"
        "# schema that is behind head.\n"
        "set -eu\n"
        "cd /app\n"
        "python -m alembic upgrade head\n"
        'exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"\n'
    )


def lifecycle_declaration() -> Dict[str, Any]:
    return {
        "schema_version": "data_lifecycle.v1",
        "migrations": {
            "tool": "alembic",
            "source": (
                "RoleRunner emission, patterned on unused kit "
                "backend/app/factory/kits/private_estate_operations/"
                "steward_runtime/migrations"
            ),
            "revisions": [REVISION_0001, REVISION_0002],
            "applied_at": "deploy entrypoint + FastAPI lifespan",
            "storage_path": "/app/data",
            "database": "platform.db",
        },
        "durability": {
            "journal_mode": "WAL",
            "synchronous": "NORMAL",
            "busy_timeout_ms": SQLITE_BUSY_TIMEOUT_MS,
            "connect_timeout_s": SQLITE_CONNECT_TIMEOUT_S,
            "fastapi_sync_threadpool": FASTAPI_SYNC_THREADPOOL,
            "writers": 1,
        },
        "backup": {
            "api": "sqlite3.Connection.backup",
            "default_dir": "$STORAGE_PATH/backups",
            "retention": BACKUP_KEEP,
            "restore_drill": "tests/test_data_lifecycle.py performs backup→wipe→restore",
        },
        "sqlite_on_mounted_disk": True,
        "spof": (
            "Render persistent disk is single-instance and the live SQLite "
            "file lives on it. One writer. Losing the disk loses the live "
            "database. Same-disk backups do not survive disk loss; set "
            "BACKUP_DIR onto another volume if disk loss is in scope."
        ),
        "capacity": {
            "disk_gb": DISK_SIZE_GB,
            "practical_sqlite": (
                "Bound by the 1 GiB Render disk declared in render.yaml, "
                "not SQLite's theoretical file limit."
            ),
            "ha": False,
            "replicas": 0,
        },
    }


def render_lifecycle_doc() -> str:
    return json.dumps(lifecycle_declaration(), indent=2, sort_keys=True) + "\n"


def render_product_tests(specs: Dict[str, Dict[str, Any]]) -> str:
    entity, sample = first_entity_sample(specs)
    entities = [spec["entity"] for spec in table_specs(specs)]
    return f'''"""S10 data lifecycle — performed, not configured.

Schema up/down on a populated v1 DB, a restore drill (backup → wipe →
restore → assert rows), and parallel writes at the FastAPI sync threadpool
size. connect() must not CREATE TABLE.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from app import backup, store
from app.migrations import current_revision, downgrade, upgrade_head, upgrade_to

ENTITY = {entity!r}
SAMPLE = {sample!r}
ENTITIES = {entities!r}
REV_V1 = {REVISION_0001!r}
REV_V2 = {REVISION_0002!r}
AUDIT = {AUDIT_TABLE!r}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path


def _tables() -> set[str]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {{r[0] for r in rows}}
    finally:
        conn.close()


def test_store_source_has_no_create_table_if_not_exists():
    src = Path(__file__).resolve().parents[1] / "app" / "store.py"
    text = src.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" not in text
    assert "PRAGMA journal_mode=WAL" in text
    assert "busy_timeout" in text


def test_connect_does_not_create_domain_tables(isolated_db):
    conn = store.connect()
    try:
        names = {{
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }}
    finally:
        conn.close()
    for name in ENTITIES:
        assert name not in names, name


@pytest.mark.skipif(not ENTITY, reason="no domain entity to migrate")
def test_schema_change_applies_to_populated_v1_and_rolls_back(isolated_db):
    assert upgrade_to(REV_V1) == REV_V1
    saved = store.save(ENTITY, dict(SAMPLE))
    assert saved["id"] is not None
    assert store.get(ENTITY, saved["id"]) is not None
    assert ENTITY in _tables()
    assert AUDIT not in _tables()

    assert upgrade_head() == REV_V2
    fetched = store.get(ENTITY, saved["id"])
    assert fetched is not None, "v1 row did not survive upgrade to v2"
    for key, value in SAMPLE.items():
        assert fetched[key] == value
    assert AUDIT in _tables()
    assert current_revision() == REV_V2

    assert downgrade(REV_V1) == REV_V1
    rolled = store.get(ENTITY, saved["id"])
    assert rolled is not None, "v1 row did not survive downgrade"
    for key, value in SAMPLE.items():
        assert rolled[key] == value
    assert AUDIT not in _tables()
    assert current_revision() == REV_V1


@pytest.mark.skipif(not ENTITY, reason="no domain entity to restore")
def test_restore_drill_backup_wipe_restore_rows(isolated_db):
    upgrade_head()
    original = [store.save(ENTITY, dict(SAMPLE)) for _ in range(3)]
    ids = [row["id"] for row in original]
    archive = backup.create_backup()
    assert archive.is_file() and archive.stat().st_size > 0

    backup.wipe_database()
    assert not store.db_path().exists()

    backup.restore_backup(archive)
    assert store.db_path().exists()
    restored_ids = [row["id"] for row in store.list_all(ENTITY)]
    assert restored_ids == ids
    for row in original:
        fetched = store.get(ENTITY, row["id"])
        assert fetched is not None
        for key, value in SAMPLE.items():
            assert fetched[key] == value


@pytest.mark.skipif(not ENTITY, reason="no domain entity to write")
def test_parallel_writes_match_fastapi_threadpool(isolated_db):
    upgrade_head()
    workers = store.FASTAPI_SYNC_THREADPOOL

    def _write(i: int):
        payload = dict(SAMPLE)
        if "reference" in payload:
            payload["reference"] = f"s10-{{i}}"
        return store.save(ENTITY, payload)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_write, i) for i in range(workers)]
        results = [f.result() for f in as_completed(futures)]
    assert len(results) == workers
    assert all(r["id"] is not None for r in results)
    persisted = store.list_all(ENTITY)
    assert len(persisted) == workers
    assert {{r["id"] for r in persisted}} == {{r["id"] for r in results}}


def test_wal_and_busy_timeout_after_migrate(isolated_db):
    upgrade_head()
    conn = store.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"
    assert int(timeout) >= store.SQLITE_BUSY_TIMEOUT_MS


def test_retention_prunes_old_backups(isolated_db):
    upgrade_head()
    store.save(ENTITY, dict(SAMPLE)) if ENTITY else None
    root = backup.backup_root()
    root.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (root / f"platform-2026080{{i}}T000000Z.db").write_bytes(b"stub")
    removed = backup.prune_backups(keep=3)
    remaining = sorted(p.name for p in backup.list_backups() if p.stat().st_size == 4)
    assert len(removed) == 3
    assert remaining == [
        "platform-20260803T000000Z.db",
        "platform-20260804T000000Z.db",
        "platform-20260805T000000Z.db",
    ]
'''


def emit_writer_artifacts(workspace: Any, specs: Dict[str, Dict[str, Any]]) -> None:
    """Write persistence, Alembic, backup, entrypoint, and the SPOF doc."""
    workspace.write_text(Path("app") / "store.py", render_store(specs))
    workspace.write_text(Path("app") / "migrations.py", render_migrations())
    workspace.write_text(Path("app") / "backup.py", render_backup())
    workspace.write_text("alembic.ini", render_alembic_ini())
    workspace.write_text(Path("alembic") / "env.py", render_alembic_env())
    workspace.write_text(Path("alembic") / "script.py.mako", render_script_mako())
    workspace.write_text(
        Path("alembic") / "versions" / "0001_baseline.py",
        render_revision_0001(specs),
    )
    workspace.write_text(
        Path("alembic") / "versions" / "0002_lifecycle_audit.py",
        render_revision_0002(),
    )
    workspace.write_text(Path("docs") / "data_lifecycle.json", render_lifecycle_doc())
    workspace.write_text(Path("scripts") / "entrypoint.sh", render_entrypoint())


def assert_no_connect_time_ddl(store_source: str) -> None:
    if "CREATE TABLE IF NOT EXISTS" in store_source:
        raise ValueError("store.py still emits CREATE TABLE IF NOT EXISTS")


def migration_table_names(revision_0001_source: str) -> set[str]:
    created: set[str] = set()
    token = "op.create_table("
    idx = 0
    while True:
        found = revision_0001_source.find(token, idx)
        if found < 0:
            break
        after = revision_0001_source[found + len(token) :]
        quote = after.find('"')
        if quote < 0:
            break
        end = after.find('"', quote + 1)
        created.add(after[quote + 1 : end])
        idx = found + len(token)
    return created
