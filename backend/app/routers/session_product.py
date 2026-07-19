"""Session-scoped product architecture API (Design Product mode).

POST /v1/sessions/{id}/product/draft
POST /v1/sessions/{id}/product/plan
POST /v1/sessions/{id}/product/approve
POST /v1/sessions/{id}/product/generate
GET  /v1/sessions/{id}/product
POST /v1/sessions/{id}/product/mode
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.session_store import get_session, update_session
from app.factory.blueprint import BlueprintError, ProductBlueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.paths import factory_repo_root
from app.factory.product_architect import (
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
)
from app.models.session import ProductDesignState

router = APIRouter()


class DraftBody(BaseModel):
    brief: str = Field(..., min_length=1)
    vertical_hint: Optional[str] = None


class ApproveBody(BaseModel):
    approve: bool = True
    blueprint: Optional[Dict[str, Any]] = None


class GenerateBody(BaseModel):
    output_dir: Optional[str] = None


class ModeBody(BaseModel):
    mode: Literal["kit", "product"]


def _require_session(session_id: str):
    state = get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    return state


def _session_output(session_id: str, product_id: str) -> Path:
    root = factory_repo_root()
    return root / "factory_outputs" / "sessions" / session_id / product_id


@router.get("/{session_id}/product")
def get_product_design(session_id: str) -> Dict[str, Any]:
    state = _require_session(session_id)
    pd = state.product_design
    yaml_text = None
    if pd.blueprint:
        try:
            yaml_text = blueprint_to_yaml(ProductBlueprint.model_validate(pd.blueprint))
        except Exception:  # noqa: BLE001
            yaml_text = None
    return {
        "session_id": session_id,
        "mode": pd.mode,
        "brief": pd.brief,
        "blueprint": pd.blueprint,
        "blueprint_yaml": yaml_text,
        "plan": pd.plan,
        "blueprint_approved": pd.blueprint_approved,
        "generation": pd.generation,
        "last_error": pd.last_error,
    }


@router.post("/{session_id}/product/mode")
def set_product_mode(session_id: str, body: ModeBody) -> Dict[str, Any]:
    state = _require_session(session_id)
    state.product_design.mode = body.mode
    update_session(session_id, state)
    return {"ok": True, "mode": body.mode}


@router.post("/{session_id}/product/draft")
def draft_product(session_id: str, body: DraftBody) -> Dict[str, Any]:
    state = _require_session(session_id)
    try:
        bp = draft_blueprint_from_brief(body.brief, vertical_hint=body.vertical_hint)
        state.product_design.brief = body.brief
        state.product_design.blueprint = bp.model_dump(mode="json")
        state.product_design.plan = None
        state.product_design.blueprint_approved = False
        state.product_design.generation = None
        state.product_design.last_error = None
        state.product_design.mode = "product"
        update_session(session_id, state)
        return {
            "ok": True,
            "blueprint": state.product_design.blueprint,
            "yaml": blueprint_to_yaml(bp),
            "source": "golden_steward"
            if bp.product_id == "cerebrum-steward"
            else "drafted",
        }
    except (BlueprintError, DualRegistryError, Exception) as exc:
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/product/plan")
def plan_product(session_id: str) -> Dict[str, Any]:
    state = _require_session(session_id)
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="draft a blueprint first")
    blocks = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    blocks_root = Path(blocks) if blocks else None
    try:
        bp = ProductBlueprint.model_validate(state.product_design.blueprint)
        plan = plan_blueprint(bp, blocks_root=blocks_root)
        state.product_design.plan = plan.to_dict()
        state.product_design.last_error = None
        update_session(session_id, state)
        return {"ok": True, "plan": state.product_design.plan}
    except DualRegistryError as exc:
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/product/approve")
def approve_blueprint(session_id: str, body: ApproveBody) -> Dict[str, Any]:
    state = _require_session(session_id)
    if body.blueprint is not None:
        try:
            bp = ProductBlueprint.model_validate(body.blueprint)
            state.product_design.blueprint = bp.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint to approve")
    state.product_design.blueprint_approved = bool(body.approve)
    update_session(session_id, state)
    return {
        "ok": True,
        "blueprint_approved": state.product_design.blueprint_approved,
    }


@router.post("/{session_id}/product/generate")
def generate_approved_product(
    session_id: str, body: Optional[GenerateBody] = None
) -> Dict[str, Any]:
    state = _require_session(session_id)
    body = body or GenerateBody()
    if not state.product_design.blueprint_approved:
        raise HTTPException(status_code=400, detail="approve blueprint before generate")
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    blocks = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    blocks_root = Path(blocks) if blocks else None
    try:
        bp = ProductBlueprint.model_validate(state.product_design.blueprint)
        if not state.product_design.plan:
            state.product_design.plan = plan_blueprint(bp, blocks_root=blocks_root).to_dict()
        out = (
            Path(body.output_dir)
            if body.output_dir
            else _session_output(session_id, bp.product_id)
        )
        # Also mirror Steward golden to canonical factory_outputs path
        result = generate_product(bp, out, blocks_root=blocks_root)
        if bp.product_id == "cerebrum-steward":
            canonical = factory_repo_root() / "factory_outputs" / "Cerebrum-Steward"
            generate_product(bp, canonical, blocks_root=blocks_root)
            result["canonical_output"] = str(canonical)
        state.product_design.generation = {
            "output_dir": result["output_dir"],
            "inputs_hash": result["inputs_hash"],
            "product_id": result["product_id"],
            "canonical_output": result.get("canonical_output"),
        }
        state.product_design.last_error = None
        update_session(session_id, state)
        return {
            "ok": True,
            "blueprint": state.product_design.blueprint,
            "plan": state.product_design.plan,
            "generation": state.product_design.generation,
        }
    except DualRegistryError as exc:
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
