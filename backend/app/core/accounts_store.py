"""Account storage for CerebrumDev.ai.

Two backends, one interface:
- sqlite (default): single file at ``ACCOUNTS_DB_PATH`` or
  ``STORAGE_PATH/accounts.db``
- Postgres: set ``ACCOUNTS_DATABASE_URL`` (Render Postgres connection string;
  ``postgres://`` is normalized to the psycopg driver)

All credentials are stored as SHA-256 hashes; passwords use PBKDF2-HMAC-SHA256.
Token types: login (``cdt_``, 7d) · API key (``cdk_``) · email verification
(``cdv_``, 24h, single use) · password reset (``cdr_``, 1h, single use).

Billing (P3): every new account starts a ``TRIAL_DAYS``-day free trial
(``subscription_status='trialing'`` + ``trial_ends_at``). ``set_subscription``
is the seam the Stripe webhook uses to flip accounts to ``active``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import sqlalchemy as sa

_LOCK = threading.RLock()
_ENGINES: Dict[str, sa.engine.Engine] = {}
_PBKDF2_ITERATIONS = 200_000
_LOGIN_TOKEN_TTL = timedelta(days=7)
_VERIFY_TOKEN_TTL = timedelta(hours=24)
_RESET_TOKEN_TTL = timedelta(hours=1)

_META = sa.MetaData()

_t_accounts = sa.Table(
    "accounts",
    _META,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("email", sa.String(254), nullable=False, unique=True),
    sa.Column("password_hash", sa.String(256), nullable=False),
    sa.Column("email_verified", sa.Boolean, nullable=False, default=False),
    sa.Column("verify_token_hash", sa.String(128), nullable=True),
    sa.Column("verify_expires_at", sa.String(64), nullable=True),
    sa.Column("reset_token_hash", sa.String(128), nullable=True),
    sa.Column("reset_expires_at", sa.String(64), nullable=True),
    sa.Column("trial_ends_at", sa.String(64), nullable=True),
    sa.Column("subscription_status", sa.String(32), nullable=True),
    sa.Column("stripe_customer_id", sa.String(128), nullable=True),
    sa.Column("stripe_subscription_id", sa.String(128), nullable=True),
    sa.Column("created_at", sa.String(64), nullable=False),
)
_t_api_keys = sa.Table(
    "api_keys",
    _META,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("account_id", sa.String(64), nullable=False, index=True),
    sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
    sa.Column("label", sa.String(128), nullable=True),
    sa.Column("created_at", sa.String(64), nullable=False),
    sa.Column("revoked_at", sa.String(64), nullable=True),
)
_t_login_tokens = sa.Table(
    "login_tokens",
    _META,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("account_id", sa.String(64), nullable=False, index=True),
    sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
    sa.Column("created_at", sa.String(64), nullable=False),
    sa.Column("expires_at", sa.String(64), nullable=False),
)
_t_session_owners = sa.Table(
    "session_owners",
    _META,
    sa.Column("session_id", sa.String(128), primary_key=True),
    sa.Column("account_id", sa.String(64), nullable=False, index=True),
    sa.Column("created_at", sa.String(64), nullable=False),
)


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


def trial_days() -> int:
    """Length of the free trial for new accounts (env ``TRIAL_DAYS``, default 3)."""
    raw = os.getenv("TRIAL_DAYS", "").strip()
    if not raw:
        return 3
    try:
        return int(raw)
    except ValueError:
        return 3


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


def _database_url() -> str:
    pg = os.getenv("ACCOUNTS_DATABASE_URL", "").strip()
    if pg:
        if pg.startswith("postgres://"):
            pg = "postgresql://" + pg[len("postgres://"):]
        if pg.startswith("postgresql://") and "+psycopg" not in pg:
            pg = pg.replace("postgresql://", "postgresql+psycopg://", 1)
        return pg
    return "sqlite:///" + _db_path()


def _ensure_column(conn: sa.engine.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column to a pre-existing table (cheap pilot migration)."""
    cols = {c["name"] for c in sa.inspect(conn).get_columns(table)}
    if column not in cols:
        conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _engine() -> sa.engine.Engine:
    url = _database_url()
    with _LOCK:
        eng = _ENGINES.get(url)
        if eng is None:
            eng = sa.create_engine(url, pool_pre_ping=True)
            with eng.begin() as conn:
                _META.create_all(conn, checkfirst=True)
                _ensure_column(conn, "accounts", "reset_token_hash", "reset_token_hash TEXT")
                _ensure_column(conn, "accounts", "reset_expires_at", "reset_expires_at TEXT")
                _ensure_column(conn, "accounts", "trial_ends_at", "trial_ends_at TEXT")
                _ensure_column(conn, "accounts", "subscription_status", "subscription_status TEXT")
                _ensure_column(conn, "accounts", "stripe_customer_id", "stripe_customer_id TEXT")
                _ensure_column(conn, "accounts", "stripe_subscription_id", "stripe_subscription_id TEXT")
            _ENGINES[url] = eng
        return eng


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


def _row_to_account(row: Any) -> Dict[str, Any]:
    m = row._mapping
    return {
        "account_id": m["id"],
        "email": m["email"],
        "email_verified": bool(m["email_verified"]),
        "created_at": m["created_at"],
    }


def create_account(email: str, password: str) -> Dict[str, Any]:
    """Create an account; raises ValueError('email_registered') on duplicate.

    New accounts start a free trial: ``subscription_status='trialing'`` with
    ``trial_ends_at`` set ``trial_days()`` days in the future.
    """
    email_norm = email.strip().lower()
    account_id = f"acct_{uuid.uuid4().hex[:16]}"
    salt = secrets.token_hex(16)
    with _LOCK, _engine().begin() as conn:
        try:
            conn.execute(
                sa.insert(_t_accounts).values(
                    id=account_id,
                    email=email_norm,
                    password_hash=_hash_password(password, salt),
                    email_verified=False,
                    trial_ends_at=_iso(_utcnow() + timedelta(days=trial_days())),
                    subscription_status="trialing",
                    stripe_customer_id=None,
                    stripe_subscription_id=None,
                    created_at=_iso(_utcnow()),
                )
            )
        except sa.exc.IntegrityError as exc:
            raise ValueError("email_registered") from exc
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.id == account_id)
        ).first()
    return _row_to_account(row)


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    email_norm = email.strip().lower()
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.email == email_norm)
        ).first()
    if row is None or not _verify_password(password, row._mapping["password_hash"]):
        return None
    return _row_to_account(row)


def get_account(account_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.id == account_id)
        ).first()
    return _row_to_account(row) if row else None


def subscription_fields(account_id: str) -> Optional[Dict[str, Any]]:
    """Raw billing fields for an account (None if the account does not exist)."""
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.id == account_id)
        ).first()
    if row is None:
        return None
    m = row._mapping
    return {
        "account_id": m["id"],
        "subscription_status": m["subscription_status"],
        "trial_ends_at": m["trial_ends_at"],
        "stripe_customer_id": m["stripe_customer_id"],
        "stripe_subscription_id": m["stripe_subscription_id"],
    }


def account_for_stripe_customer(customer_id: str) -> Optional[Dict[str, Any]]:
    """Resolve an account from a Stripe customer id (webhook path)."""
    if not customer_id:
        return None
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(
                _t_accounts.c.stripe_customer_id == customer_id.strip()
            )
        ).first()
    if row is None:
        return None
    m = row._mapping
    return {
        "account_id": m["id"],
        "email": m["email"],
        "subscription_status": m["subscription_status"],
        "stripe_customer_id": m["stripe_customer_id"],
        "stripe_subscription_id": m["stripe_subscription_id"],
    }


def set_subscription(
    account_id: str,
    status: str,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
) -> bool:
    """Set subscription state — the Stripe webhook seam (also used by ops/tests).

    Status values: ``trialing`` · ``active`` · ``past_due`` · ``canceled``.
    Returns True when the account exists.
    """
    values: Dict[str, Any] = {"subscription_status": status.strip().lower()}
    if stripe_customer_id is not None:
        values["stripe_customer_id"] = stripe_customer_id.strip()
    if stripe_subscription_id is not None:
        values["stripe_subscription_id"] = stripe_subscription_id.strip()
    with _LOCK, _engine().begin() as conn:
        result = conn.execute(
            sa.update(_t_accounts).where(_t_accounts.c.id == account_id).values(**values)
        )
        return result.rowcount > 0


def issue_login_token(account_id: str) -> str:
    raw = "cdt_" + secrets.token_urlsafe(32)
    with _LOCK, _engine().begin() as conn:
        conn.execute(
            sa.insert(_t_login_tokens).values(
                id=f"tok_{uuid.uuid4().hex[:16]}",
                account_id=account_id,
                token_hash=_hash_token(raw),
                created_at=_iso(_utcnow()),
                expires_at=_iso(_utcnow() + _LOGIN_TOKEN_TTL),
            )
        )
    return raw


def account_for_login_token(raw: str) -> Optional[Dict[str, Any]]:
    if not raw or not raw.startswith("cdt_"):
        return None
    token_hash = _hash_token(raw)
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_login_tokens).where(_t_login_tokens.c.token_hash == token_hash)
        ).first()
        if row is None:
            return None
        expires = _parse(row._mapping["expires_at"])
        if expires is not None and expires <= _utcnow():
            return None
        acct = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.id == row._mapping["account_id"])
        ).first()
    return _row_to_account(acct) if acct else None


def issue_api_key(account_id: str, label: str = "") -> Dict[str, str]:
    raw = "cdk_" + secrets.token_urlsafe(24)
    key_id = f"key_{uuid.uuid4().hex[:16]}"
    with _LOCK, _engine().begin() as conn:
        conn.execute(
            sa.insert(_t_api_keys).values(
                id=key_id,
                account_id=account_id,
                key_hash=_hash_token(raw),
                label=label.strip()[:128],
                created_at=_iso(_utcnow()),
                revoked_at=None,
            )
        )
    return {"key_id": key_id, "api_key": raw}


def account_for_api_key(raw: str) -> Optional[Dict[str, Any]]:
    if not raw or not raw.startswith("cdk_"):
        return None
    key_hash = _hash_token(raw)
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_api_keys).where(_t_api_keys.c.key_hash == key_hash)
        ).first()
        if row is None or row._mapping["revoked_at"]:
            return None
        acct = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.id == row._mapping["account_id"])
        ).first()
    return _row_to_account(acct) if acct else None


def list_api_keys(account_id: str) -> List[Dict[str, Any]]:
    with _LOCK, _engine().begin() as conn:
        rows = conn.execute(
            sa.select(_t_api_keys)
            .where(_t_api_keys.c.account_id == account_id)
            .order_by(_t_api_keys.c.created_at)
        ).all()
    return [
        {
            "key_id": r._mapping["id"],
            "label": r._mapping["label"] or "",
            "created_at": r._mapping["created_at"],
            "revoked": bool(r._mapping["revoked_at"]),
        }
        for r in rows
    ]


def revoke_api_key(account_id: str, key_id: str) -> bool:
    with _LOCK, _engine().begin() as conn:
        result = conn.execute(
            sa.update(_t_api_keys)
            .where(
                _t_api_keys.c.id == key_id,
                _t_api_keys.c.account_id == account_id,
                _t_api_keys.c.revoked_at.is_(None),
            )
            .values(revoked_at=_iso(_utcnow()))
        )
        return result.rowcount > 0


def issue_verify_token(account_id: str) -> str:
    raw = "cdv_" + secrets.token_urlsafe(24)
    with _LOCK, _engine().begin() as conn:
        conn.execute(
            sa.update(_t_accounts)
            .where(_t_accounts.c.id == account_id)
            .values(
                verify_token_hash=_hash_token(raw),
                verify_expires_at=_iso(_utcnow() + _VERIFY_TOKEN_TTL),
            )
        )
    return raw


def confirm_verify_token(raw: str) -> Optional[str]:
    """Mark the owning account verified; return account_id or None."""
    if not raw or not raw.startswith("cdv_"):
        return None
    token_hash = _hash_token(raw)
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.verify_token_hash == token_hash)
        ).first()
        if row is None:
            return None
        expires = _parse(row._mapping["verify_expires_at"])
        if expires is not None and expires <= _utcnow():
            return None
        conn.execute(
            sa.update(_t_accounts)
            .where(_t_accounts.c.id == row._mapping["id"])
            .values(email_verified=True, verify_token_hash=None, verify_expires_at=None)
        )
        return row._mapping["id"]


def issue_reset_token(email: str) -> Optional[str]:
    """Issue a password-reset token if the email is registered; else None."""
    email_norm = email.strip().lower()
    raw = "cdr_" + secrets.token_urlsafe(24)
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.email == email_norm)
        ).first()
        if row is None:
            return None
        conn.execute(
            sa.update(_t_accounts)
            .where(_t_accounts.c.id == row._mapping["id"])
            .values(
                reset_token_hash=_hash_token(raw),
                reset_expires_at=_iso(_utcnow() + _RESET_TOKEN_TTL),
            )
        )
    return raw


def confirm_reset_token(raw: str, new_password: str) -> Optional[str]:
    """Reset the password and invalidate all login tokens; return account_id."""
    if not raw or not raw.startswith("cdr_"):
        return None
    token_hash = _hash_token(raw)
    salt = secrets.token_hex(16)
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.reset_token_hash == token_hash)
        ).first()
        if row is None:
            return None
        expires = _parse(row._mapping["reset_expires_at"])
        if expires is not None and expires <= _utcnow():
            return None
        account_id = row._mapping["id"]
        conn.execute(
            sa.update(_t_accounts)
            .where(_t_accounts.c.id == account_id)
            .values(
                password_hash=_hash_password(new_password, salt),
                reset_token_hash=None,
                reset_expires_at=None,
            )
        )
        # Security: a password reset signs out every existing session.
        conn.execute(
            sa.delete(_t_login_tokens).where(_t_login_tokens.c.account_id == account_id)
        )
        return account_id


def record_session_owner(session_id: str, account_id: str) -> None:
    with _LOCK, _engine().begin() as conn:
        existing = conn.execute(
            sa.select(_t_session_owners).where(
                _t_session_owners.c.session_id == session_id
            )
        ).first()
        if existing is None:
            conn.execute(
                sa.insert(_t_session_owners).values(
                    session_id=session_id,
                    account_id=account_id,
                    created_at=_iso(_utcnow()),
                )
            )
        else:
            conn.execute(
                sa.update(_t_session_owners)
                .where(_t_session_owners.c.session_id == session_id)
                .values(account_id=account_id)
            )


def session_owner(session_id: str) -> Optional[str]:
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_session_owners).where(
                _t_session_owners.c.session_id == session_id
            )
        ).first()
    return row._mapping["account_id"] if row else None


def sessions_for_owner(account_id: str) -> List[str]:
    with _LOCK, _engine().begin() as conn:
        rows = conn.execute(
            sa.select(_t_session_owners.c.session_id)
            .where(_t_session_owners.c.account_id == account_id)
            .order_by(_t_session_owners.c.created_at.desc())
        ).all()
    return [r._mapping["session_id"] for r in rows]


def all_session_ids() -> List[str]:
    with _LOCK, _engine().begin() as conn:
        rows = conn.execute(
            sa.select(_t_session_owners.c.session_id).order_by(
                _t_session_owners.c.created_at.desc()
            )
        ).all()
    return [r._mapping["session_id"] for r in rows]
