"""Pilot bearer authentication and estate authorization."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.steward.auth_models import EstateAccess, PilotAccessToken, PilotPrincipal
from app.steward.config import get_config
from app.steward.db import get_session, init_engine
from app.steward.errors import safe_http_exception


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def token_pepper() -> str:
    return os.getenv("STEWARD_TOKEN_PEPPER", "steward-pilot-dev-pepper-change-me")


def hash_token(raw_token: str) -> str:
    digest = hmac.new(
        token_pepper().encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def generate_raw_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    principal_id: str
    display_name: str
    role: str
    tenant_id: str
    estate_id: str
    estate_role: str
    auth_mode: str  # bearer|demo_bypass


def _parse_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _lookup_token(session: Session, raw_token: str) -> Optional[PilotAccessToken]:
    token_hash = hash_token(raw_token)
    row = session.execute(
        select(PilotAccessToken).where(PilotAccessToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= _utcnow():
        return None
    return row


def _lookup_principal(session: Session, principal_id: str) -> Optional[PilotPrincipal]:
    return session.execute(
        select(PilotPrincipal).where(PilotPrincipal.id == principal_id)
    ).scalar_one_or_none()


def _lookup_estate_access(
    session: Session,
    *,
    principal_id: str,
    tenant_id: str,
    estate_id: str,
) -> Optional[EstateAccess]:
    return session.execute(
        select(EstateAccess).where(
            EstateAccess.principal_id == principal_id,
            EstateAccess.tenant_id == tenant_id,
            EstateAccess.estate_id == estate_id,
        )
    ).scalar_one_or_none()


def authorize_estate(
    session: Session,
    *,
    principal: AuthenticatedPrincipal,
    tenant_id: str,
    estate_id: str,
    require_curator: bool = False,
) -> AuthenticatedPrincipal:
    tenant = (tenant_id or "").strip()
    estate = (estate_id or "").strip()
    if not tenant or not estate:
        raise safe_http_exception(
            400,
            code="estate_scope_required",
            message="Tenant and estate scope are required",
        )
    if principal.tenant_id != tenant or principal.estate_id != estate:
        raise safe_http_exception(
            403,
            code="estate_access_denied",
            message="Principal is not authorized for the requested estate scope",
        )
    if require_curator and principal.estate_role not in {"curator", "admin"}:
        if principal.role != "admin":
            raise safe_http_exception(
                403,
                code="curator_required",
                message="Curator role required for this operation",
            )
    return principal


def resolve_principal(
    session: Session,
    *,
    authorization: Optional[str],
    x_tenant_id: Optional[str],
    x_estate_id: Optional[str],
) -> AuthenticatedPrincipal:
    cfg = get_config()
    raw = _parse_bearer(authorization)

    if raw:
        token_row = _lookup_token(session, raw)
        if token_row is None:
            raise safe_http_exception(
                401,
                code="invalid_token",
                message="Invalid or expired access token",
            )
        principal_row = _lookup_principal(session, token_row.principal_id)
        if principal_row is None or not principal_row.enabled:
            raise safe_http_exception(
                403,
                code="principal_disabled",
                message="Principal is disabled",
            )
        tenant = (x_tenant_id or "").strip()
        estate = (x_estate_id or "").strip()
        if not tenant or not estate:
            raise safe_http_exception(
                400,
                code="estate_scope_required",
                message="X-Tenant-Id and X-Estate-Id headers are required",
            )
        access = _lookup_estate_access(
            session,
            principal_id=principal_row.id,
            tenant_id=tenant,
            estate_id=estate,
        )
        if access is None:
            raise safe_http_exception(
                403,
                code="estate_access_denied",
                message="Principal is not authorized for the requested estate scope",
            )
        return AuthenticatedPrincipal(
            principal_id=principal_row.id,
            display_name=principal_row.display_name,
            role=principal_row.role,
            tenant_id=tenant,
            estate_id=estate,
            estate_role=access.role,
            auth_mode="bearer",
        )

    if cfg.allow_demo_auth_bypass:
        tenant = (x_tenant_id or "").strip() or "tenant_a"
        estate = (x_estate_id or "").strip() or "estate_a"
        return AuthenticatedPrincipal(
            principal_id="demo-bypass",
            display_name="Demo Auth Bypass",
            role="admin",
            tenant_id=tenant,
            estate_id=estate,
            estate_role="admin",
            auth_mode="demo_bypass",
        )

    raise safe_http_exception(
        401,
        code="authentication_required",
        message="Authorization bearer token required",
    )


def get_authenticated_principal(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_estate_id: Optional[str] = Header(None, alias="X-Estate-Id"),
    session: Session = Depends(get_session),
) -> AuthenticatedPrincipal:
    init_engine()
    return resolve_principal(
        session,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_estate_id=x_estate_id,
    )


def require_admin_principal(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> AuthenticatedPrincipal:
    if principal.role != "admin" and principal.estate_role != "admin":
        raise safe_http_exception(
            403,
            code="admin_required",
            message="Admin role required for this operation",
        )
    return principal


def seed_pilot_fixture(session: Session) -> dict:
    """Seed a demo principal + token for local/CI when explicitly enabled."""
    principal_id = "pilot-demo-01"
    tenant_id = "tenant_a"
    estate_id = "estate_a"
    existing = _lookup_principal(session, principal_id)
    if existing is None:
        session.add(
            PilotPrincipal(
                id=principal_id,
                display_name="Pilot Demo Principal",
                email="pilot-demo@example.invalid",
                role="admin",
                enabled=True,
            )
        )
    access = _lookup_estate_access(
        session, principal_id=principal_id, tenant_id=tenant_id, estate_id=estate_id
    )
    if access is None:
        session.add(
            EstateAccess(
                id="access-demo-01",
                principal_id=principal_id,
                tenant_id=tenant_id,
                estate_id=estate_id,
                role="admin",
            )
        )
    raw_token = os.getenv("STEWARD_PILOT_FIXTURE_TOKEN", "steward-pilot-fixture-token")
    token_hash = hash_token(raw_token)
    token_row = session.execute(
        select(PilotAccessToken).where(PilotAccessToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if token_row is None:
        session.add(
            PilotAccessToken(
                id="token-demo-01",
                principal_id=principal_id,
                token_hash=token_hash,
                label="fixture",
            )
        )
    session.commit()
    return {
        "principal_id": principal_id,
        "tenant_id": tenant_id,
        "estate_id": estate_id,
        "fixture_token": raw_token,
    }
