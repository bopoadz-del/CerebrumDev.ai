"""Factory product API: draft architecture → plan → generate.

This is the in-platform path toward an AI agent designing product architecture.
Steward may still use the predefined golden blueprint when the brief matches.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import Principal, require_api_key
from app.core.trial_limits import require_within_limit
from app.factory.blueprint import BlueprintError, ProductBlueprint, load_blueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.paths import UnsafeOutputDir, factory_repo_root, safe_output_dir
from app.factory.product_architect import (
    architect_pipeline,
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
    steward_golden_path,
)

router = APIRouter()


class DraftRequest(BaseModel):
    brief: str = Field(..., min_length=1)
    vertical_hint: Optional[str] = None


class PlanRequest(BaseModel):
    blueprint: Dict[str, Any]


class GenerateRequest(BaseModel):
    blueprint: Optional[Dict[str, Any]] = None
    brief: Optional[str] = None
    vertical_hint: Optional[str] = None
    output_dir: Optional[str] = None


def _default_output(product_id: str) -> Path:
    return factory_repo_root() / "factory_outputs" / product_id


@router.get("/golden/steward")
def get_steward_golden() -> Dict[str, Any]:
    bp = load_blueprint(steward_golden_path())
    return bp.model_dump(mode="json")


@router.post("/draft")
def draft_product_architecture(
    body: DraftRequest, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    # Drafting is a paid LLM call (and retries once against a fallback model),
    # so it is metered exactly like generation/chat/export. The gate runs
    # BEFORE the spend and outside the try/except below, which would otherwise
    # rewrite the 429 into a 400 (TrialLimitExceeded is an HTTPException).
    require_within_limit(principal.account_id, "draft")
    try:
        bp = draft_blueprint_from_brief(
            body.brief, vertical_hint=body.vertical_hint
        )
    except BlueprintError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "blueprint": bp.model_dump(mode="json"),
        "yaml": blueprint_to_yaml(bp),
        "source": "golden_steward"
        if bp.product_id == "cerebrum-steward"
        else "drafted",
    }


@router.post("/plan")
def plan_product(
    body: PlanRequest, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    # The planner runs over caller-supplied blueprint JSON, so it is an
    # unauthenticated-shaped compute path unless metered. Same counter as
    # draft, and again outside the try/except so the 429 survives.
    require_within_limit(principal.account_id, "draft")
    try:
        bp = ProductBlueprint.model_validate(body.blueprint)
        plan = plan_blueprint(bp)
    except (BlueprintError, DualRegistryError, Exception) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "plan": plan.to_dict()}


@router.post("/generate")
def generate_from_architecture(
    body: GenerateRequest, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    # Server-side trial boundary: generations are metered per account.
    require_within_limit(principal.account_id, "generation")
    blocks = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    blocks_root = Path(blocks) if blocks else None
    try:
        if body.blueprint:
            bp = ProductBlueprint.model_validate(body.blueprint)
            out = safe_output_dir(body.output_dir, bp.product_id)
            plan = plan_blueprint(bp, blocks_root=blocks_root)
            result = generate_product(bp, out, blocks_root=blocks_root)
            return {
                "ok": True,
                "blueprint": bp.model_dump(mode="json"),
                "plan": plan.to_dict(),
                "generation": {
                    "output_dir": result["output_dir"],
                    "inputs_hash": result["inputs_hash"],
                    "product_id": result["product_id"],
                },
            }
        if not body.brief:
            raise HTTPException(status_code=400, detail="brief or blueprint required")
        out = safe_output_dir(body.output_dir, "cerebrum-steward")
        result = architect_pipeline(
            body.brief,
            out,
            vertical_hint=body.vertical_hint,
            blocks_root=blocks_root,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "generate failed"))
        return result
    except UnsafeOutputDir as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DualRegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BlueprintError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
