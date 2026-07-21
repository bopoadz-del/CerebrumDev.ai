"""Persist audit events to the append-only PostgreSQL ledger."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.steward.audit_models import AuditEvent


def persist_audit_event(
    session: Session,
    *,
    tenant_id: str,
    action_id: str,
    request_id: str,
    status: str,
    user_id: str,
    estate_id: Optional[str] = None,
    principal_id: Optional[str] = None,
    detail: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one audit event and return a serializable record."""
    row = AuditEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        estate_id=estate_id,
        action_id=action_id,
        request_id=request_id,
        status=status,
        user_id=user_id,
        principal_id=principal_id,
        detail=detail,
        meta=metadata or {},
    )
    session.add(row)
    session.flush()
    return {
        "id": row.id,
        "action_id": row.action_id,
        "request_id": row.request_id,
        "status": row.status,
        "tenant_id": row.tenant_id,
        "estate_id": row.estate_id,
        "user_id": row.user_id,
        "principal_id": row.principal_id,
        "recorded_at": row.recorded_at.isoformat(),
        "detail": row.detail,
        "metadata": dict(row.meta or {}),
    }
