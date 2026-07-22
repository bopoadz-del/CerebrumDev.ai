"""Auth for CerebrumDev.ai backend — master key + per-user credentials.

Resolution order for every request:
1. ``CEREBRUM_DEV_API_KEY`` (master/admin key) — full access, unchanged.
2. Per-account API key (``cdk_…``) via Bearer or X-API-Key.
3. Per-account login token (``cdt_…``) via Bearer.
4. No master key configured (local dev) → open dev principal, as before.

A startup check in ``main.py`` refuses to boot in production without the
master key. Account credentials never replace it — they add user identity.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Request

_API_KEY = os.getenv("CEREBRUM_DEV_API_KEY", "").strip()


@dataclass(frozen=True)
class Principal:
    """Who is calling. ``kind``: admin (master key) | user (account) | dev (open)."""

    kind: str
    account_id: Optional[str] = None
    email: Optional[str] = None


def verify_production_auth() -> None:
    """Refuse to boot in production without an API key.

    In local development (ENV != production) auth remains optional.
    """
    if os.getenv("ENV") == "production" and not os.getenv("CEREBRUM_DEV_API_KEY", "").strip():
        raise RuntimeError("CEREBRUM_DEV_API_KEY must be set in production")


def _provided_token(authorization: Optional[str], x_api_key: Optional[str]) -> str:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = token.strip()
            if token:
                return token
    if x_api_key:
        return x_api_key.strip()
    return ""


def _resolve_user_principal(token: str) -> Optional[Principal]:
    from . import accounts_store

    account = accounts_store.account_for_api_key(token)
    if account is None:
        account = accounts_store.account_for_login_token(token)
    if account is None:
        return None
    return Principal(kind="user", account_id=account["account_id"], email=account["email"])


def require_api_key(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    """Dependency: master key, per-account credential, or dev-open.

    Returns the resolved :class:`Principal`; routers that need identity
    depend on this same function (FastAPI dedupes it per request).
    """
    provided = _provided_token(authorization, x_api_key)

    if _API_KEY:
        if provided == _API_KEY:
            return Principal(kind="admin")
        principal = _resolve_user_principal(provided) if provided else None
        if principal is not None:
            return principal
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Local-dev open mode (no master key configured): honor account credentials
    # when presented, otherwise fall back to the anonymous dev principal.
    if provided:
        principal = _resolve_user_principal(provided)
        if principal is not None:
            return principal
    return Principal(kind="dev")
