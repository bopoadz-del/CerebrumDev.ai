"""Provisioning helpers for tenants, users and projects."""

from __future__ import annotations

import re
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.retailops.models import Project, Tenant, User


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def ensure_tenant(session: Session, tenant_id: str, name: Optional[str] = None) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        tenant = Tenant(id=tenant_id, name=name or tenant_id)
        session.add(tenant)
        session.flush()
    return tenant


def ensure_user(
    session: Session,
    user_id: str,
    tenant_id: str,
    *,
    email: Optional[str] = None,
    name: Optional[str] = None,
    permissions: Optional[list] = None,
) -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            tenant_id=tenant_id,
            email=email,
            name=name,
            permissions=permissions or [],
        )
        session.add(user)
        session.flush()
    elif permissions is not None:
        user.permissions = permissions
        session.flush()
    return user


def ensure_project(
    session: Session,
    *,
    tenant_id: str,
    canonical_name: str,
    name: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Project:
    existing = get_project_by_canonical(session, tenant_id, canonical_name)
    if existing is not None:
        return existing
    project = Project(
        id=project_id or f"prj_{_slug(canonical_name)[:24]}_{uuid.uuid4().hex[:6]}",
        tenant_id=tenant_id,
        canonical_name=canonical_name,
        name=name or canonical_name,
    )
    session.add(project)
    session.flush()
    return project


def get_project_by_canonical(
    session: Session, tenant_id: str, canonical_name: str
) -> Optional[Project]:
    stmt = select(Project).where(
        Project.tenant_id == tenant_id,
        Project.canonical_name == canonical_name,
    )
    return session.execute(stmt).scalars().first()
