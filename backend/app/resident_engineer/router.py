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


class EscalateBody(BaseModel):
    product_id: str
    dna_version: str = "1.0.0"
    symptom: str
    target: str = "runtime"
    failed_action_id: Optional[str] = None
    submit_to_intake: bool = True


@router.post("/change-request")
async def resident_emit_change_request(body: EscalateBody) -> Dict[str, Any]:
    """Emit a signed REPAIR change-request (flag-gated; M3 paperwork).

    Used when L2 heals dead-end and Resident escalates to Factory intake.
    """
    _require_enabled()
    from app.change_requests.emit import EmitRejected, emit_repair_from_escalation

    try:
        return emit_repair_from_escalation(
            product_id=body.product_id,
            dna_version=body.dna_version,
            symptom=body.symptom,
            target=body.target,
            failed_action_id=body.failed_action_id,
            submit_to_intake=body.submit_to_intake,
        )
    except EmitRejected as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        # Optional L2→REPAIR escalation (off unless RESIDENT_EMIT_CHANGE_REQUESTS)
        escalation = None
        try:
            from app.change_requests.emit import EmitRejected, emit_repair_from_escalation
            from app.change_requests.flags import resident_emit_change_requests_enabled

            if resident_emit_change_requests_enabled():
                escalation = emit_repair_from_escalation(
                    product_id=body.tenant_id or "unknown-product",
                    dna_version="1.0.0",
                    symptom=str(exc),
                    failed_action_id=body.action_id,
                    submit_to_intake=True,
                )
        except Exception:  # noqa: BLE001 — escalation must not mask heal error
            escalation = None
        detail: Dict[str, Any] = {"error": str(exc)}
        if escalation:
            detail["escalation"] = escalation
        raise HTTPException(status_code=409, detail=detail) from exc