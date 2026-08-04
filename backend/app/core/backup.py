"""Consistent snapshots of the durable state, and the restore path.

Why this exists: every account, password hash, API key, upload and generated
product lives on a single Render disk with no copy anywhere. Losing that disk
loses the business. This module produces a verified snapshot and, just as
importantly, can put one back -- a backup nobody has restored is not a backup,
it is a hope.

Two scopes, deliberately separate:

* the accounts database, which is small, structured, and the thing whose loss
  is unrecoverable (nobody can re-derive a password hash);
* the bulk content on disk -- uploads, sessions, generated products -- which is
  large and, for most of it, regenerable.

The accounts snapshot uses SQLite's online backup API rather than copying the
file, because copying a live SQLite database can capture a torn write. For a
Postgres-backed deployment (``ACCOUNTS_DATABASE_URL`` set) the snapshot shells
out to ``pg_dump``.

**Destination matters.** Writing a snapshot to the same disk it came from
protects against logical loss -- an accidental delete, a bad migration, the
arbitrary-rmtree class of bug -- but not against losing the disk. Set
``BACKUP_DIR`` to a path on a different volume, or run the platform against
managed Postgres with point-in-time recovery, if disk loss is in scope.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_KEEP = 14


def _utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def storage_root() -> Path:
    return Path(os.getenv("STORAGE_PATH", "./storage"))


def backup_root() -> Path:
    """Where snapshots are written. Override with BACKUP_DIR."""
    override = os.getenv("BACKUP_DIR", "").strip()
    if override:
        return Path(override)
    return storage_root() / "backups"


def accounts_database_url() -> str:
    raw = os.getenv("ACCOUNTS_DATABASE_URL", "").strip()
    return raw


def accounts_sqlite_path() -> Path:
    """Resolve the SQLite accounts DB the same way accounts_store does."""
    override = os.getenv("ACCOUNTS_DB_PATH", "").strip()
    if override:
        return Path(override)
    return storage_root() / "accounts.db"


@dataclass
class BackupResult:
    ok: bool
    archive: Optional[Path] = None
    engine: str = "sqlite"
    bytes_written: int = 0
    included: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "archive": str(self.archive) if self.archive else None,
            "engine": self.engine,
            "bytes_written": self.bytes_written,
            "included": list(self.included),
            "skipped": dict(self.skipped),
            "error": self.error,
        }


def snapshot_sqlite(source: Path, dest: Path) -> None:
    """Copy a live SQLite database consistently.

    Uses the online backup API. A plain file copy of an active database can
    capture a partially written page, which produces an archive that restores
    without error and is subtly corrupt -- the worst possible failure mode for
    a backup.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def snapshot_postgres(url: str, dest: Path) -> None:
    """Dump a Postgres accounts database with pg_dump."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["pg_dump", "--no-owner", "--no-privileges", "--format=custom", "--file", str(dest), url],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        # stderr can carry the connection string; report the code, not the text.
        raise RuntimeError(f"pg_dump failed with exit code {proc.returncode}")


def verify_sqlite_snapshot(path: Path) -> Dict[str, int]:
    """Open a snapshot and count rows per table.

    This is the difference between "a file was produced" and "a database was
    produced". It runs on every backup, so a snapshot that cannot be opened
    fails the job rather than sitting on disk looking like protection.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"snapshot failed integrity check: {integrity}")
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        counts: Dict[str, int] = {}
        for t in tables:
            counts[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        return counts
    finally:
        conn.close()


def create_backup(
    *,
    dest_root: Optional[Path] = None,
    include_content: bool = True,
) -> BackupResult:
    """Produce one timestamped archive of the durable state."""
    dest_root = Path(dest_root) if dest_root else backup_root()
    dest_root.mkdir(parents=True, exist_ok=True)
    stamp = _utcstamp()
    result = BackupResult(ok=False)

    with tempfile.TemporaryDirectory() as staging_str:
        staging = Path(staging_str)

        pg_url = accounts_database_url()
        if pg_url:
            result.engine = "postgres"
            try:
                snapshot_postgres(pg_url, staging / "accounts.dump")
                result.included.append("accounts.dump")
            except Exception as exc:  # noqa: BLE001
                result.error = f"accounts dump failed: {exc}"
                return result
        else:
            result.engine = "sqlite"
            src = accounts_sqlite_path()
            if src.is_file():
                try:
                    snapshot_sqlite(src, staging / "accounts.db")
                    counts = verify_sqlite_snapshot(staging / "accounts.db")
                    result.included.append("accounts.db")
                    result.skipped.pop("accounts.db", None)
                    # Row counts go in the manifest so a restore can be checked
                    # against what was actually captured.
                    (staging / "MANIFEST.txt").write_text(
                        "\n".join(f"{t}={n}" for t, n in sorted(counts.items())),
                        encoding="utf-8",
                    )
                except Exception as exc:  # noqa: BLE001
                    result.error = f"accounts snapshot failed: {exc}"
                    return result
            else:
                result.skipped["accounts.db"] = f"not found at {src}"

        if include_content:
            root = storage_root()
            # factory_outputs: generated platforms are the deliverable; they
            # live under STORAGE_PATH in production (factory/paths.py) and
            # must be part of the snapshot.
            for name in ("uploads", "sessions", "chroma", "factory_outputs"):
                src_dir = root / name
                if src_dir.is_dir():
                    shutil.copytree(src_dir, staging / name, dirs_exist_ok=True)
                    result.included.append(name)
                else:
                    result.skipped[name] = "absent"

        archive = dest_root / f"cerebrumdev-backup-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for child in sorted(staging.iterdir()):
                tar.add(child, arcname=child.name)

        result.archive = archive
        result.bytes_written = archive.stat().st_size
        result.ok = True

    return result


def restore_backup(archive: Path, target_root: Path) -> Dict[str, Any]:
    """Unpack an archive into ``target_root`` and verify the accounts DB.

    Restores to an explicit target rather than over the live location, so that
    testing a restore can never destroy production. Promoting a verified
    restore is a deliberate, separate act.
    """
    archive = Path(archive)
    target_root = Path(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            # Refuse absolute paths and traversal in archive members.
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError(f"refusing unsafe archive member: {name}")
        tar.extractall(target_root)

    out: Dict[str, Any] = {"ok": True, "target": str(target_root), "verified": None}
    restored_db = target_root / "accounts.db"
    if restored_db.is_file():
        out["verified"] = verify_sqlite_snapshot(restored_db)
    return out


def prune_backups(dest_root: Optional[Path] = None, keep: int = DEFAULT_KEEP) -> List[Path]:
    """Delete all but the newest ``keep`` archives. Returns what was removed."""
    dest_root = Path(dest_root) if dest_root else backup_root()
    if not dest_root.is_dir():
        return []
    archives = sorted(
        (p for p in dest_root.glob("cerebrumdev-backup-*.tar.gz") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    removed: List[Path] = []
    for stale in archives[keep:]:
        stale.unlink()
        removed.append(stale)
    return removed
