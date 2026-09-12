"""Restore accounts from on-disk backup archives into ACCOUNTS_DATABASE_URL.

Nightly snapshots live under ``STORAGE_PATH/backups`` as
``cerebrumdev-backup-*.tar.gz``. A healthy Postgres-backed run stores
``accounts.dump`` (pg_dump custom) or ``accounts.sql`` (SQLAlchemy INSERT
fallback). SQLite-era archives store ``accounts.db``.

This module lists those archives and merges dump rows into live Postgres
without wiping smoke accounts. ``prefer_source`` parks a conflicting live
email so a historical owner id (``acct_c38ae401…``) can reclaim it.

``cerebrum-builds`` is not the accounts store.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core import backup as bk
from scripts.migrate_accounts_to_postgres import (
    HARD_VERIFY_TABLES,
    TABLES,
    AccountsRestoreError,
    _RESTORE_LOCK,
    _assert_no_secrets,
    list_sqlite_account_identities,
    migrate,
    require_accounts_database_url,
    run_restore,
    target_engine,
    verify_source_present,
)

_ARCHIVE_RE = re.compile(r"^cerebrumdev-backup-\d{8}T\d{6}Z\.tar\.gz$")
_INSERT_RE = re.compile(
    r'^INSERT INTO (?P<table>"?[\w.]+"?) \((?P<cols>.+)\) VALUES \((?P<vals>.+)\);\s*$'
)
_COPY_HEAD_RE = re.compile(
    r"^COPY\s+(?P<table>[\w.\"]+)\s+\((?P<cols>.+)\)\s+FROM\s+stdin;\s*$",
    re.IGNORECASE,
)
_DUMP_MEMBERS = ("accounts.dump", "accounts.sql", "accounts.db")


def _member_basename(name: str) -> str:
    return Path(name).name


def resolve_backup_archive(name: Optional[str]) -> Path:
    """Resolve a backup basename under ``backup_root()``. Rejects traversal."""
    root = bk.backup_root().resolve()
    if not name or name in {"latest", "auto"}:
        listing = list_backup_archives()
        recommended = listing.get("recommended")
        if not recommended:
            raise AccountsRestoreError(
                "archive_missing",
                f"no backup archive with an accounts dump under {root}",
                extra={"root": str(root)},
            )
        name = recommended
    base = Path(str(name)).name
    if not _ARCHIVE_RE.match(base):
        raise AccountsRestoreError(
            "archive_unsafe",
            "archive must be a cerebrumdev-backup-YYYYMMDDThhmmssZ.tar.gz basename",
            extra={"archive": base},
        )
    path = (root / base).resolve()
    if path.parent != root or not path.is_file():
        raise AccountsRestoreError(
            "archive_missing",
            f"archive not found under backup root: {base}",
            extra={"archive": base, "root": str(root)},
        )
    return path


def _safe_tar_members(archive: Path) -> List[str]:
    with tarfile.open(archive, "r:gz") as tar:
        names: List[str] = []
        for member in tar.getmembers():
            raw = member.name
            if raw.startswith("/") or ".." in Path(raw).parts:
                raise AccountsRestoreError(
                    "archive_unsafe",
                    f"refusing unsafe archive member: {raw}",
                    extra={"archive": archive.name},
                )
            names.append(raw)
        return names


def _extract_member(archive: Path, dest: Path, wanted: Iterable[str]) -> Dict[str, Path]:
    wanted_set = {Path(w).name for w in wanted}
    found: Dict[str, Path] = {}
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise AccountsRestoreError(
                    "archive_unsafe",
                    f"refusing unsafe archive member: {member.name}",
                )
            base = _member_basename(member.name)
            if base not in wanted_set or not member.isfile():
                continue
            extracted = dest / base
            src = tar.extractfile(member)
            if src is None:
                continue
            extracted.write_bytes(src.read())
            found[base] = extracted
    return found


def _split_sql_ident_list(raw: str) -> List[str]:
    return [part.strip().strip('"') for part in raw.split(",") if part.strip()]


def _split_sql_values(raw: str) -> List[Any]:
    out: List[Any] = []
    i = 0
    n = len(raw)
    while i < n:
        while i < n and raw[i] in " \t":
            i += 1
        if i >= n:
            break
        if raw.startswith("NULL", i) and (i + 4 == n or raw[i + 4] in ",)"):
            out.append(None)
            i += 4
        elif raw.startswith("TRUE", i) and (i + 4 == n or raw[i + 4] in ",)"):
            out.append(True)
            i += 4
        elif raw.startswith("FALSE", i) and (i + 5 == n or raw[i + 5] in ",)"):
            out.append(False)
            i += 5
        elif raw[i] == "'":
            i += 1
            buf: List[str] = []
            while i < n:
                if raw[i] == "'" and i + 1 < n and raw[i + 1] == "'":
                    buf.append("'")
                    i += 2
                elif raw[i] == "'":
                    i += 1
                    break
                else:
                    buf.append(raw[i])
                    i += 1
            out.append("".join(buf))
        else:
            j = i
            while j < n and raw[j] != ",":
                j += 1
            token = raw[i:j].strip()
            if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
                out.append(int(token))
            else:
                try:
                    out.append(float(token))
                except ValueError:
                    out.append(token)
            i = j
        while i < n and raw[i] in " \t":
            i += 1
        if i < n and raw[i] == ",":
            i += 1
    return out


def _table_key(raw: str) -> str:
    name = raw.strip().strip('"')
    if "." in name:
        name = name.split(".")[-1].strip('"')
    return name


def parse_insert_sql(text: str) -> Dict[str, List[Dict[str, Any]]]:
    data: Dict[str, List[Dict[str, Any]]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("INSERT INTO"):
            continue
        match = _INSERT_RE.match(stripped)
        if not match:
            continue
        table = _table_key(match.group("table"))
        if table not in TABLES:
            continue
        cols = _split_sql_ident_list(match.group("cols"))
        vals = _split_sql_values(match.group("vals"))
        if len(cols) != len(vals):
            raise AccountsRestoreError(
                "dump_unreadable",
                f"INSERT column/value mismatch for {table}",
            )
        data.setdefault(table, []).append(dict(zip(cols, vals)))
    return data


def _unescape_copy_field(raw: str) -> Any:
    if raw == r"\N":
        return None
    out: List[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def parse_copy_sql(text: str) -> Dict[str, List[Dict[str, Any]]]:
    data: Dict[str, List[Dict[str, Any]]] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _COPY_HEAD_RE.match(lines[i].strip())
        if not match:
            i += 1
            continue
        table = _table_key(match.group("table"))
        cols = _split_sql_ident_list(match.group("cols"))
        i += 1
        rows: List[Dict[str, Any]] = []
        while i < len(lines) and lines[i] != r"\.":
            fields = lines[i].split("\t")
            if table in TABLES and len(fields) == len(cols):
                row: Dict[str, Any] = {}
                for col, raw in zip(cols, fields):
                    value = _unescape_copy_field(raw)
                    if col == "email_verified":
                        if value in {"t", "true", "TRUE", "1"}:
                            value = True
                        elif value in {"f", "false", "FALSE", "0"}:
                            value = False
                    row[col] = value
                rows.append(row)
            i += 1
        if table in TABLES:
            data.setdefault(table, []).extend(rows)
        i += 1
    return data


def parse_accounts_sql(text: str) -> Dict[str, List[Dict[str, Any]]]:
    inserted = parse_insert_sql(text)
    copied = parse_copy_sql(text)
    merged: Dict[str, List[Dict[str, Any]]] = {}
    for table in TABLES:
        rows = (inserted.get(table) or []) + (copied.get(table) or [])
        if rows:
            merged[table] = rows
    return merged


def _sniff_dump(path: Path) -> str:
    raw = path.read_bytes()[:32]
    if raw.startswith(b"SQLite format 3"):
        return "sqlite"
    if raw.startswith(b"PGDMP"):
        return "pg_dump"
    try:
        head = raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        head = ""
    if "INSERT INTO" in head or head.startswith("--") or head.startswith("BEGIN"):
        return "sql"
    text = path.read_text(encoding="utf-8", errors="ignore")[:400]
    if "INSERT INTO" in text or "COPY " in text:
        return "sql"
    return "unknown"


def _pg_restore_to_sql(dump: Path, dest: Path) -> Path:
    exe = shutil.which("pg_restore")
    if not exe:
        raise AccountsRestoreError(
            "dump_unreadable",
            "accounts.dump is a custom pg_dump and pg_restore is not on PATH",
            extra={"dump": dump.name},
        )
    proc = subprocess.run(
        [
            exe,
            "--file",
            str(dest),
            "--data-only",
            "--no-owner",
            "--no-privileges",
            str(dump),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        err = " ".join((proc.stderr or proc.stdout or "").split())[:240]
        raise AccountsRestoreError(
            "dump_unreadable",
            f"pg_restore failed for {dump.name}: {err or 'empty output'}",
        )
    return dest


def load_accounts_data(path: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """Return (table→rows, dump_kind)."""
    kind = _sniff_dump(path)
    if path.name == "accounts.db" or kind == "sqlite":
        return read_sqlite_tables(path), "accounts.db"
    if kind == "sql" or path.suffix == ".sql":
        text = path.read_text(encoding="utf-8")
        data = parse_accounts_sql(text)
        if not data.get("accounts"):
            raise AccountsRestoreError(
                "dump_unreadable",
                f"{path.name} has no accounts INSERT/COPY rows",
            )
        return data, "accounts.sql"
    if kind == "pg_dump" or path.name == "accounts.dump":
        with tempfile.TemporaryDirectory() as tmp:
            sql_path = Path(tmp) / "restored.sql"
            _pg_restore_to_sql(path, sql_path)
            data = parse_accounts_sql(sql_path.read_text(encoding="utf-8"))
        if not data.get("accounts"):
            raise AccountsRestoreError(
                "dump_unreadable",
                f"{path.name} restored to SQL with no accounts rows",
            )
        return data, "accounts.dump"
    raise AccountsRestoreError(
        "dump_unreadable",
        f"unrecognized accounts artifact: {path.name}",
    )


def read_sqlite_tables(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: Dict[str, List[Dict[str, Any]]] = {}
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


def _identities_from_data(data: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    rows = []
    for row in data.get("accounts") or []:
        account_id = str(row.get("id") or "")
        email = str(row.get("email") or "")
        if account_id or email:
            rows.append({"id": account_id, "email": email})
    rows.sort(key=lambda r: (r["email"], r["id"]))
    return rows


def _peek_archive_accounts(archive: Path, members: List[str]) -> List[Dict[str, str]]:
    bases = {_member_basename(n) for n in members}
    for candidate in _DUMP_MEMBERS:
        if candidate not in bases:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            extracted = _extract_member(archive, Path(tmp), [candidate])
            path = extracted.get(candidate)
            if path is None:
                continue
            try:
                if candidate == "accounts.db" or _sniff_dump(path) == "sqlite":
                    return list_sqlite_account_identities(path)
                if _sniff_dump(path) == "sql" or candidate == "accounts.sql":
                    data = parse_accounts_sql(path.read_text(encoding="utf-8"))
                    return _identities_from_data(data)
            except Exception:  # noqa: BLE001 — listing must not 500
                return []
    return []


def list_backup_archives() -> Dict[str, Any]:
    root = bk.backup_root()
    archives: List[Dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.glob("cerebrumdev-backup-*.tar.gz"), reverse=True):
            if not path.is_file() or not _ARCHIVE_RE.match(path.name):
                continue
            try:
                members = _safe_tar_members(path)
            except AccountsRestoreError:
                continue
            bases = {_member_basename(n) for n in members}
            item = {
                "name": path.name,
                "bytes": path.stat().st_size,
                "accounts_dump": "accounts.dump" in bases,
                "accounts_sql": "accounts.sql" in bases,
                "accounts_db": "accounts.db" in bases,
                "members": sorted(bases),
                "source_accounts": _peek_archive_accounts(path, members),
            }
            item["has_accounts_artifact"] = bool(
                item["accounts_dump"] or item["accounts_sql"] or item["accounts_db"]
            )
            archives.append(item)

    recommended = None
    for key in ("accounts_dump", "accounts_sql", "accounts_db"):
        for item in archives:
            if item.get(key):
                recommended = item["name"]
                break
        if recommended:
            break

    payload = {
        "root": str(root),
        "count": len(archives),
        "recommended": recommended,
        "archives": archives,
        "note": (
            "On-disk backup archives only. cerebrum-builds is not the accounts "
            "store. POST /v1/ops/accounts-restore/from-backup?archive=<name> "
            "merges dump rows into ACCOUNTS_DATABASE_URL."
        ),
    }
    _assert_no_secrets(payload)
    return payload


def run_restore_from_backup(
    *,
    archive: Optional[str] = None,
    force: bool = True,
    dry_run: bool = False,
    prefer_source: bool = True,
    verify: bool = True,
) -> Dict[str, Any]:
    """Merge accounts from a backup archive into ACCOUNTS_DATABASE_URL."""
    with _RESTORE_LOCK:
        require_accounts_database_url()
        path = resolve_backup_archive(archive)
        with tempfile.TemporaryDirectory() as tmp:
            extracted = _extract_member(path, Path(tmp), _DUMP_MEMBERS)
            artifact = None
            for name in _DUMP_MEMBERS:
                if name in extracted:
                    artifact = extracted[name]
                    break
            if artifact is None:
                raise AccountsRestoreError(
                    "dump_missing",
                    f"{path.name} has no accounts.dump, accounts.sql, or accounts.db",
                    extra={"archive": path.name},
                )
            data, dump_kind = load_accounts_data(artifact)
            engine = target_engine()
            try:
                report = migrate(
                    engine,
                    data,
                    force=force,
                    dry_run=dry_run,
                    prefer_source=prefer_source,
                )
                report.source = "backup"
                report.archive = path.name
                report.dump_kind = dump_kind
                report.sqlite_path = str(artifact)
                report.sqlite_present = dump_kind == "accounts.db"
                if verify and not dry_run:
                    missing = verify_source_present(
                        engine, data, report.email_conflicts
                    )
                    hard = {
                        table: keys
                        for table, keys in missing.items()
                        if table in HARD_VERIFY_TABLES
                    }
                    advisory = {
                        table: keys
                        for table, keys in missing.items()
                        if table not in HARD_VERIFY_TABLES
                    }
                    report.missing_advisory = {
                        table: [list(k) for k in keys]
                        for table, keys in advisory.items()
                    }
                    report.verified = not hard
                    if hard:
                        report.ok = False
                        raise AccountsRestoreError(
                            "verify_failed",
                            "source account primary keys missing after backup merge",
                            extra={
                                **report.as_public_dict(),
                                "missing_keys": {
                                    table: [list(k) for k in keys]
                                    for table, keys in hard.items()
                                },
                            },
                        )
                payload = report.as_public_dict()
                payload["source_accounts"] = _identities_from_data(data)
                _assert_no_secrets(payload)
                return payload
            finally:
                engine.dispose()


# Keep the live-disk SQLite entry importable from one place.
run_restore_from_sqlite = run_restore
