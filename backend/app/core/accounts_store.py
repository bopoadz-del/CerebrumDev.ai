"""Account storage for CerebrumDev.ai — stdlib sqlite3, zero new dependencies.

Pilot-grade persistence: a single-file database, by default at
``STORAGE_PATH/accounts.db``. Set ``ACCOUNTS_DB_PATH`` to a file on a
persistent disk in production (Render's default filesystem is ephemeral).
Move to managed Postgres later by swapping this module; callers must not
rely on sqlite specifics.

Four credential types, all stored as SHA-256 hashes:
- login tokens (``cdt_``) — 7-day, issued to the web UI on login
- API keys (``cdk_``) — long-lived, per-account programmatic access
- email verification tokens (``cdv_``) — 24h, single use
- password reset tokens (``cdr_``) — 1h, single use, kills all login tokens
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_PBKDF2_ITERATIONS = 200_000
_LOGIN_TOKEN_TTL = timedelta(days=7)
_VERIFY_TOKEN_TTL = timedelta(hours=24)
_RESET_TOKEN_TTL = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _db_path() -> str:
    override = os.getenv("ACCOUNTS_DB_PATH", "").strip()
    if override:
        parent = os.path.dirname(override)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return override
    storage = os.getenv("STORAGE_PATH", "./storage")
    os.makedirs(storage, exist_ok=True)
    return os.path.join(storage, "accounts.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table (cheap pilot migration)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            email_verified INTEGER NOT NULL DEFAULT 0,
            verify_token_hash TEXT,
            verify_expires_at TEXT,
            reset_token_hash TEXT,
            reset_expires_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            label TEXT,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS login_tokens (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_owners (
            session_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    # Existing databases created before reset support.
    _ensure_column(conn, "accounts", "reset_token_hash", "reset_token_hash TEXT")
    _ensure_column(conn, "accounts", "reset_expires_at", "reset_expires_at TEXT")
    conn.commit()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def _row_to_account(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "account_id": row["id"],
        "email": row["email"],
        "email_verified": bool(row["email_verified"]),
        "created_at": row["created_at"],
    }


def create_account(email: str, password: str) -> Dict[str, Any]:
    """Create an account; raises ValueError('email_registered') on duplicate."""
    email_norm = email.strip().lower()
    account_id = f"acct_{uuid.uuid4().hex[:16]}"
    salt = secrets.token_hex(16)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            try:
                conn.execute(
                    "INSERT INTO accounts (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (account_id, email_norm, _hash_password(password, salt), _iso(_utcnow())),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("email_registered") from exc
            conn.commit()
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        finally:
            conn.close()
    return _row_to_account(row)


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    email_norm = email.strip().lower()
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email_norm,)).fetchone()
        finally:
            conn.close()
    if row is None or not _verify_password(password, row["password_hash"]):
        return None
    return _row_to_account(row)


def get_account(account_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        finally:
            conn.close()
    return _row_to_account(row) if row else None


def issue_login_token(account_id: str) -> str:
    raw = "cdt_" + secrets.token_urlsafe(32)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            conn.execute(
                "INSERT INTO login_tokens (id, account_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (
                    f"tok_{uuid.uuid4().hex[:16]}",
                    account_id,
                    _hash_token(raw),
                    _iso(_utcnow()),
                    _iso(_utcnow() + _LOGIN_TOKEN_TTL),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return raw


def account_for_login_token(raw: str) -> Optional[Dict[str, Any]]:
    if not raw or not raw.startswith("cdt_"):
        return None
    token_hash = _hash_token(raw)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute(
                "SELECT account_id, expires_at FROM login_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            expires = _parse(row["expires_at"])
            if expires is not None and expires <= _utcnow():
                return None
            acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (row["account_id"],)).fetchone()
        finally:
            conn.close()
    return _row_to_account(acct) if acct else None


def issue_api_key(account_id: str, label: str = "") -> Dict[str, str]:
    raw = "cdk_" + secrets.token_urlsafe(24)
    key_id = f"key_{uuid.uuid4().hex[:16]}"
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            conn.execute(
                "INSERT INTO api_keys (id, account_id, key_hash, label, created_at) VALUES (?, ?, ?, ?, ?)",
                (key_id, account_id, _hash_token(raw), label.strip()[:128], _iso(_utcnow())),
            )
            conn.commit()
        finally:
            conn.close()
    return {"key_id": key_id, "api_key": raw}


def account_for_api_key(raw: str) -> Optional[Dict[str, Any]]:
    if not raw or not raw.startswith("cdk_"):
        return None
    key_hash = _hash_token(raw)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute(
                "SELECT account_id, revoked_at FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            if row is None or row["revoked_at"]:
                return None
            acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (row["account_id"],)).fetchone()
        finally:
            conn.close()
    return _row_to_account(acct) if acct else None


def list_api_keys(account_id: str) -> List[Dict[str, Any]]:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            rows = conn.execute(
                "SELECT id, label, created_at, revoked_at FROM api_keys WHERE account_id = ? ORDER BY created_at",
                (account_id,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "key_id": r["id"],
            "label": r["label"] or "",
            "created_at": r["created_at"],
            "revoked": bool(r["revoked_at"]),
        }
        for r in rows
    ]


def revoke_api_key(account_id: str, key_id: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            cur = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND account_id = ? AND revoked_at IS NULL",
                (_iso(_utcnow()), key_id, account_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def issue_verify_token(account_id: str) -> str:
    raw = "cdv_" + secrets.token_urlsafe(24)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            conn.execute(
                "UPDATE accounts SET verify_token_hash = ?, verify_expires_at = ? WHERE id = ?",
                (_hash_token(raw), _iso(_utcnow() + _VERIFY_TOKEN_TTL), account_id),
            )
            conn.commit()
        finally:
            conn.close()
    return raw


def confirm_verify_token(raw: str) -> Optional[str]:
    """Mark the owning account verified; return account_id or None."""
    if not raw or not raw.startswith("cdv_"):
        return None
    token_hash = _hash_token(raw)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute(
                "SELECT id, verify_expires_at FROM accounts WHERE verify_token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            expires = _parse(row["verify_expires_at"])
            if expires is not None and expires <= _utcnow():
                return None
            conn.execute(
                "UPDATE accounts SET email_verified = 1, verify_token_hash = NULL, verify_expires_at = NULL WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            return row["id"]
        finally:
            conn.close()


def issue_reset_token(email: str) -> Optional[str]:
    """Issue a password-reset token if the email is registered; else None."""
    email_norm = email.strip().lower()
    raw = "cdr_" + secrets.token_urlsafe(24)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute("SELECT id FROM accounts WHERE email = ?", (email_norm,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE accounts SET reset_token_hash = ?, reset_expires_at = ? WHERE id = ?",
                (_hash_token(raw), _iso(_utcnow() + _RESET_TOKEN_TTL), row["id"]),
            )
            conn.commit()
        finally:
            conn.close()
    return raw


def confirm_reset_token(raw: str, new_password: str) -> Optional[str]:
    """Reset the password and invalidate all login tokens; return account_id."""
    if not raw or not raw.startswith("cdr_"):
        return None
    token_hash = _hash_token(raw)
    salt = secrets.token_hex(16)
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute(
                "SELECT id, reset_expires_at FROM accounts WHERE reset_token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            expires = _parse(row["reset_expires_at"])
            if expires is not None and expires <= _utcnow():
                return None
            conn.execute(
                "UPDATE accounts SET password_hash = ?, reset_token_hash = NULL, reset_expires_at = NULL WHERE id = ?",
                (_hash_password(new_password, salt), row["id"]),
            )
            # Security: a password reset signs out every existing session.
            conn.execute("DELETE FROM login_tokens WHERE account_id = ?", (row["id"],))
            conn.commit()
            return row["id"]
        finally:
            conn.close()


def record_session_owner(session_id: str, account_id: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO session_owners (session_id, account_id, created_at) VALUES (?, ?, ?)",
                (session_id, account_id, _iso(_utcnow())),
            )
            conn.commit()
        finally:
            conn.close()


def session_owner(session_id: str) -> Optional[str]:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            row = conn.execute(
                "SELECT account_id FROM session_owners WHERE session_id = ?", (session_id,)
            ).fetchone()
        finally:
            conn.close()
    return row["account_id"] if row else None


def sessions_for_owner(account_id: str) -> List[str]:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            rows = conn.execute(
                "SELECT session_id FROM session_owners WHERE account_id = ? ORDER BY created_at DESC",
                (account_id,),
            ).fetchall()
        finally:
            conn.close()
    return [r["session_id"] for r in rows]


def all_session_ids() -> List[str]:
    with _LOCK:
        conn = _connect()
        try:
            _init_schema(conn)
            rows = conn.execute(
                "SELECT session_id FROM session_owners ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
    return [r["session_id"] for r in rows]
