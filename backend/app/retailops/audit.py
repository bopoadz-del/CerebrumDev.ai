"""Audit persistence for action executions.

Persists an ``action_runs`` record for every execution. Secrets are never
stored: only an input hash, output metadata, dependencies, evidence counts,
warnings and error codes are retained.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.retailops.kit.contract.models import ActionContext, ActionResult
from app.retailops.models import ActionRun


def input_hash(arguments: Dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def persist_action_run(
    session: Session,
    *,
    result: ActionResult,
    context: ActionContext,
    arguments: Dict[str, Any],
) -> ActionRun:
    run = ActionRun(
        request_id=result.request_id,
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        user_id=context.user_id,
        action_id=result.action_id,
        kit_version=result.kit_version,
        status=result.status.value,
        started_at=result.started_at,
        completed_at=result.completed_at,
        duration_ms=result.duration_ms,
        dependencies=[d.to_dict() for d in result.dependencies],
        evidence_count=len(result.evidence),
        input_hash=input_hash(arguments),
        output_metadata={
            "keys": sorted(result.output.keys()),
            "error_message": result.error_message,
        },
        warnings=list(result.warnings),
        error_code=result.error_code,
    )
    session.add(run)
    session.flush()
    return run


def list_action_runs(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    limit: int = 100,
) -> List[ActionRun]:
    stmt = (
        select(ActionRun)
        .where(ActionRun.tenant_id == tenant_id, ActionRun.project_id == project_id)
        .order_by(ActionRun.started_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())
