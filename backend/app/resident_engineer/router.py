"""Factory-side Resident Engineer routes (feature-flagged, default off)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.resident_engineer.diagnosis import build_failure_report
from app.resident_engineer.flags import resident_engineer_enabled
from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS
from app.resident_engineer.heal.executor import HealRejected, execute_heal
from app.resident_engineer.heal.validate import HealValidationError
from app.resident_engineer.modes import draft_change_request
from app.resident_engineer.observe import observe

router = APIRouter(prefix="/v1/resident", tags=["resident-engineer"])


def _require_enabled() -> None:
    if not resident_engineer_enabled():
        raise HTTPException(
            status_code=503,
            detail="RESIDENT_ENGINEER_ENABLED is false (default)",
        )


@router.get("/status")
async def resident_status() -> Dict[str, Any]:
    """Always available — reports flag + allowlist (no heal execution)."""
    return {
        "enabled": resident_engineer_enabled(),
        "mode": "resident",
        "allowlisted_heal_actions": list(ALLOWLISTED_HEAL_ACTIONS),
        "levels": {
            "L1": "observe",
            "L2": "allowlisted_heal",
            "L3_L5": "draft_change_request_only",
        },
    }


@router.get("/observe")
async def resident_observe(
    product_root: Optional[str] = None,
    log_text: str = "",
) -> Dict[str, Any]:
    _require_enabled()
    root = Path(product_root) if product_root else None
    try:
        return observe(root, log_text=log_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DiagnoseBody(BaseModel):
    symptom: str = ""
    product_root: Optional[str] = None
    related_action_ids: list[str] = Field(default_factory=list)


@router.post("/diagnose")
async def resident_diagnose(body: DiagnoseBody) -> Dict[str, Any]:
    _require_enabled()
    root = Path(body.product_root) if body.product_root else None
    return build_failure_report(
        product_root=root,
        symptom=body.symptom,
        related_action_ids=body.related_action_ids or None,
    )


class HealBody(BaseModel):
    action_id: str
    confirmed: bool = False
    tenant_id: str = "default"
    user_id: str = "operator"


@router.post("/heal")
async def resident_heal(body: HealBody) -> Dict[str, Any]:
    _require_enabled()
    try:
        return await execute_heal(
            body.action_id,
            confirmed=body.confirmed,
            tenant_id=body.tenant_id,
            user_id=body.user_id,
        )
    except HealRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HealValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class DraftBody(BaseModel):
    level: str = "L3"
    kind: str = "ExpansionRequest"
    summary: str = ""
    product_id: Optional[str] = None


@router.post("/draft-change-request")
async def resident_draft(body: DraftBody) -> Dict[str, Any]:
    _require_enabled()
    try:
        return draft_change_request(
            level=body.level,  # type: ignore[arg-type]
            kind=body.kind,
            summary=body.summary,
            product_id=body.product_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
