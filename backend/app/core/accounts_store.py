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

# Per-account usage counters for server-side trial quotas.
# ``period`` is "lifetime" or an ISO date for daily counters.
_t_usage_counters = sa.Table(
    "usage_counters",
    _META,
    sa.Column("account_id", sa.String(64), primary_key=True),
    sa.Column("counter", sa.String(64), primary_key=True),
    sa.Column("period", sa.String(32), primary_key=True),
    sa.Column("value", sa.Integer, nullable=False, default=0),
    sa.Column("updated_at", sa.String(64), nullable=False),
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


def get_account_by_email(email: str) -> Optional[Dict[str, Any]]:
    email_norm = email.strip().lower()
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.email == email_norm)
        ).first()
    return _row_to_account(row) if row else None


def ensure_verified_account(email: str, password: str) -> Dict[str, Any]:
    """Create or update an ops-only account and mark it email-verified.

    Public register never calls this. The production smoke gate uses it so a
    verified test principal can exist without turning off verification for
    real users.
    """
    email_norm = email.strip().lower()
    existing = get_account_by_email(email_norm)
    if existing is None:
        account = create_account(email_norm, password)
        account_id = account["account_id"]
    else:
        account_id = existing["account_id"]
        salt = secrets.token_hex(16)
        with _LOCK, _engine().begin() as conn:
            conn.execute(
                sa.update(_t_accounts)
                .where(_t_accounts.c.id == account_id)
                .values(password_hash=_hash_password(password, salt))
            )
    with _LOCK, _engine().begin() as conn:
        conn.execute(
            sa.update(_t_accounts)
            .where(_t_accounts.c.id == account_id)
            .values(
                email_verified=True,
                verify_token_hash=None,
                verify_expires_at=None,
            )
        )
    account = get_account(account_id)
    if account is None:
        raise RuntimeError("ensure_verified_account lost the account it just wrote")
    return account


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
        "email": m["email"],
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


def get_usage(account_id: str, counter: str, period: str) -> int:
    """Current value of one usage counter (0 when never incremented)."""
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_usage_counters.c.value).where(
                _t_usage_counters.c.account_id == account_id,
                _t_usage_counters.c.counter == counter,
                _t_usage_counters.c.period == period,
            )
        ).first()
    return int(row[0]) if row else 0


def increment_usage(account_id: str, counter: str, period: str) -> int:
    """Atomically increment one usage counter; returns the new value."""
    with _LOCK, _engine().begin() as conn:
        updated = conn.execute(
            sa.update(_t_usage_counters)
            .where(
                _t_usage_counters.c.account_id == account_id,
                _t_usage_counters.c.counter == counter,
                _t_usage_counters.c.period == period,
            )
            .values(value=_t_usage_counters.c.value + 1, updated_at=_iso(_utcnow()))
        )
        if updated.rowcount == 0:
            conn.execute(
                sa.insert(_t_usage_counters).values(
                    account_id=account_id,
                    counter=counter,
                    period=period,
                    value=1,
                    updated_at=_iso(_utcnow()),
                )
            )
            return 1
        row = conn.execute(
            sa.select(_t_usage_counters.c.value).where(
                _t_usage_counters.c.account_id == account_id,
                _t_usage_counters.c.counter == counter,
                _t_usage_counters.c.period == period,
            )
        ).first()
        return int(row[0])


def list_usage_counters(account_id: str) -> List[Dict[str, Any]]:
    """Every usage counter held for an account (data-export input)."""
    with _LOCK, _engine().begin() as conn:
        rows = conn.execute(
            sa.select(_t_usage_counters)
            .where(_t_usage_counters.c.account_id == account_id)
            .order_by(_t_usage_counters.c.counter, _t_usage_counters.c.period)
        ).all()
    return [
        {
            "counter": r._mapping["counter"],
            "period": r._mapping["period"],
            "value": int(r._mapping["value"]),
            "updated_at": r._mapping["updated_at"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Data rights: re-authentication, export, erasure, retention
# ---------------------------------------------------------------------------


def verify_account_password(account_id: str, password: str) -> bool:
    """Re-authenticate an already-identified account by password.

    ``authenticate`` resolves by email; destructive endpoints already know the
    account id from the bearer credential and must confirm the *human* is
    present. Returns False for unknown accounts, so a stolen token cannot even
    probe which ids exist.
    """
    if not account_id or not password:
        return False
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts.c.password_hash).where(_t_accounts.c.id == account_id)
        ).first()
    if row is None:
        return False
    return _verify_password(password, row[0])


def export_account(account_id: str) -> Optional[Dict[str, Any]]:
    """Everything the accounts database holds about one account.

    Field-by-field whitelist, deliberately not ``dict(row._mapping)`` minus a
    denylist: the accounts row also carries ``password_hash``,
    ``verify_token_hash`` and ``reset_token_hash``, and a denylist silently
    starts leaking the day a new secret column is added. Secret material is
    never exported — not even in hashed form, since a hash is still an offline
    cracking target and a bearer-equality oracle.
    """
    with _LOCK, _engine().begin() as conn:
        row = conn.execute(
            sa.select(_t_accounts).where(_t_accounts.c.id == account_id)
        ).first()
    if row is None:
        return None
    m = row._mapping
    return {
        "profile": {
            "account_id": m["id"],
            "email": m["email"],
            "email_verified": bool(m["email_verified"]),
            "created_at": m["created_at"],
        },
        "billing": {
            "subscription_status": m["subscription_status"],
            "trial_ends_at": m["trial_ends_at"],
            "stripe_customer_id": m["stripe_customer_id"],
            "stripe_subscription_id": m["stripe_subscription_id"],
        },
        "usage_counters": list_usage_counters(account_id),
        "sessions": sessions_for_owner(account_id),
        "api_keys": list_api_keys(account_id),
    }


def delete_account(account_id: str) -> bool:
    """Erase an account and every dependent row in one transaction.

    Order is child-rows-first so a mid-way failure rolls back to a consistent
    state rather than leaving credentials that authenticate against a missing
    account. Returns True when an account row was actually removed.
    """
    if not account_id:
        return False
    with _LOCK, _engine().begin() as conn:
        conn.execute(sa.delete(_t_api_keys).where(_t_api_keys.c.account_id == account_id))
        conn.execute(
            sa.delete(_t_login_tokens).where(_t_login_tokens.c.account_id == account_id)
        )
        conn.execute(
            sa.delete(_t_session_owners).where(
                _t_session_owners.c.account_id == account_id
            )
        )
        conn.execute(
            sa.delete(_t_usage_counters).where(
                _t_usage_counters.c.account_id == account_id
            )
        )
        result = conn.execute(sa.delete(_t_accounts).where(_t_accounts.c.id == account_id))
        return result.rowcount > 0


def _is_expired(raw: Optional[str], now: datetime) -> bool:
    """Whether an ISO timestamp is in the past. Unparseable values are not
    treated as expired — deleting rows we cannot read is worse than keeping
    them one cycle longer."""
    parsed = _parse(raw)
    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def purge_expired_tokens() -> Dict[str, int]:
    """Delete time-limited credential material whose lifetime has elapsed.

    Covers login tokens (7d) and the single-use email-verification (24h) and
    password-reset (1h) hashes parked on the account row. Nothing else here is
    genuinely time-limited: ``trial_ends_at`` and ``subscription_status`` are
    billing state, not retention state, and are left alone.

    Timestamps are compared as parsed datetimes, not strings: the stored values
    carry UTC offsets and lexical comparison of offset-bearing ISO strings is
    wrong. Callable from ops or the admin endpoint; deliberately no scheduler.
    """
    now = _utcnow()
    removed_login = 0
    cleared_verify = 0
    cleared_reset = 0
    with _LOCK, _engine().begin() as conn:
        rows = conn.execute(
            sa.select(_t_login_tokens.c.id, _t_login_tokens.c.expires_at)
        ).all()
        stale = [r._mapping["id"] for r in rows if _is_expired(r._mapping["expires_at"], now)]
        if stale:
            result = conn.execute(
                sa.delete(_t_login_tokens).where(_t_login_tokens.c.id.in_(stale))
            )
            removed_login = result.rowcount

        acct_rows = conn.execute(
            sa.select(
                _t_accounts.c.id,
                _t_accounts.c.verify_token_hash,
                _t_accounts.c.verify_expires_at,
                _t_accounts.c.reset_token_hash,
                _t_accounts.c.reset_expires_at,
            )
        ).all()
        for r in acct_rows:
            m = r._mapping
            if m["verify_token_hash"] and _is_expired(m["verify_expires_at"], now):
                conn.execute(
                    sa.update(_t_accounts)
                    .where(_t_accounts.c.id == m["id"])
                    .values(verify_token_hash=None, verify_expires_at=None)
                )
                cleared_verify += 1
            if m["reset_token_hash"] and _is_expired(m["reset_expires_at"], now):
                conn.execute(
                    sa.update(_t_accounts)
                    .where(_t_accounts.c.id == m["id"])
                    .values(reset_token_hash=None, reset_expires_at=None)
                )
                cleared_reset += 1
    return {
        "login_tokens_deleted": removed_login,
        "verify_tokens_cleared": cleared_verify,
        "reset_tokens_cleared": cleared_reset,
    }
