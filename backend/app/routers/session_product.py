"""Session-scoped product architecture API (Design Product mode).

POST /v1/sessions/{id}/product/draft
POST /v1/sessions/{id}/product/plan
POST /v1/sessions/{id}/product/approve
POST /v1/sessions/{id}/product/generate
GET  /v1/sessions/{id}/product
GET  /v1/sessions/{id}/product/package   (export the generated platform zip)
POST /v1/sessions/{id}/product/mode
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.auth import Principal, require_api_key
from app.core.llm_throttle import require_llm_rate
from app.core.session_store import get_session, update_session
from app.core.trial_limits import require_within_limit
from app.factory.blocks_source import resolve_blocks_root
from app.factory.blueprint import BlueprintError, ProductBlueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.paths import UnsafeOutputDir, factory_outputs_root, safe_output_dir
from app.factory.product_architect import (
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
)

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


def _require_session(session_id: str, principal: Principal):
    state = get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    if principal.kind == "user" and state.user_id != principal.account_id:
        # Do not leak existence across accounts (mirrors routers/sessions.py).
        raise HTTPException(status_code=404, detail="session not found")
    return state


def _session_output(session_id: str, product_id: str) -> Path:
    return factory_outputs_root() / "sessions" / session_id / product_id


def _enforce_export_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: exports are metered per account."""
    require_within_limit(account_id, "export")


def _enforce_generation_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: generations are metered per account."""
    require_within_limit(account_id, "generation")


def _enforce_draft_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: blueprint drafts are metered per account.

    Drafting is a paid LLM call that retries once against a fallback model, so
    an unmetered draft endpoint is an unbounded spend path for a free account.
    """
    require_within_limit(account_id, "draft")


@router.get("/{session_id}/product")
def get_product_design(
    session_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
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


@router.get("/{session_id}/product/package")
def download_product_package(
    session_id: str, principal: Principal = Depends(require_api_key)
) -> FileResponse:
    """Export the generated platform as a zip — the factory's deliverable."""
    state = _require_session(session_id, principal)
    _enforce_export_quota(principal.account_id)
    gen = state.product_design.generation
    if not gen or not gen.get("output_dir"):
        raise HTTPException(
            status_code=404,
            detail="no generated product — draft and approve a blueprint first",
        )
    out = Path(gen["output_dir"])
    if not out.is_dir():
        raise HTTPException(
            status_code=404,
            detail="generated product not found on disk — generate again",
        )

    # A runner build is a background job. Zipping mid-build would hand the
    # customer a splice of two writer passes, and zipping a FAILED build
    # would ship an artifact its own gates rejected. Only a succeeded build
    # is downloadable; the template engine has no build ledger and is
    # unaffected.
    from app.factory.build_jobs import build_status

    status = build_status(out)
    if status["state"] == "building":
        raise HTTPException(
            status_code=409,
            detail=(
                "the platform is still being built "
                f"({status.get('phases_done', 0)}/{status.get('phases_total', 5)} "
                "phases complete) — poll /product/build-status"
            ),
        )
    if status["state"] == "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                "the build did not pass its gates and will not be shipped: "
                + str(status.get("detail"))
            ),
        )

    archive_base = out.parent / f"{out.name}-export"
    archive = shutil.make_archive(str(archive_base), "zip", root_dir=out)
    product_id = gen.get("product_id") or out.name
    return FileResponse(
        archive,
        filename=f"cerebrumdev-{product_id}.zip",
        media_type="application/zip",
    )


@router.get("/{session_id}/product/build-status")
def get_build_status(
    session_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    """Progress of the background build. Read off the build ledger.

    Cheap and quota-free on purpose: the client polls this while the agent
    writes the platform, and metering a progress read would charge the
    customer for waiting.
    """
    from app.factory.build_jobs import build_status

    state = _require_session(session_id, principal)
    gen = state.product_design.generation
    if not gen or not gen.get("output_dir"):
        return {"ok": True, "build": {"state": "not_started"}}
    return {
        "ok": True,
        "product_id": gen.get("product_id"),
        "build": build_status(Path(gen["output_dir"])),
    }


@router.post("/{session_id}/product/mode")
def set_product_mode(
    session_id: str, body: ModeBody, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    state.product_design.mode = body.mode
    update_session(session_id, state)
    return {"ok": True, "mode": body.mode}


@router.post("/{session_id}/product/draft")
def draft_product(
    session_id: str, body: DraftBody, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    # Outside the try/except below on purpose: that handler catches bare
    # Exception and re-raises as 400, which would mask the 429.
    _enforce_draft_quota(principal.account_id)
    require_llm_rate(principal, "draft")
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
def plan_product(
    session_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="draft a blueprint first")
    require_llm_rate(principal, "plan")
    # Same resolver as the chat flow (env path, then Store clone).
    blocks_root = resolve_blocks_root()
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
def approve_blueprint(
    session_id: str, body: ApproveBody, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
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
    session_id: str,
    body: Optional[GenerateBody] = None,
    principal: Principal = Depends(require_api_key),
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    _enforce_generation_quota(principal.account_id)
    require_llm_rate(principal, "generate")
    body = body or GenerateBody()
    if not state.product_design.blueprint_approved:
        raise HTTPException(status_code=400, detail="approve blueprint before generate")
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    # Same resolver as the chat flow (env path, then Store clone).
    blocks_root = resolve_blocks_root()
    try:
        bp = ProductBlueprint.model_validate(state.product_design.blueprint)
        if not state.product_design.plan:
            state.product_design.plan = plan_blueprint(bp, blocks_root=blocks_root).to_dict()
        # A caller-supplied output_dir is a recursive-delete target inside the
        # generator, so it must stay inside factory_outputs/. None keeps the
        # per-session default.
        if body.output_dir:
            out = safe_output_dir(body.output_dir, bp.product_id)
        else:
            out = _session_output(session_id, bp.product_id)
        # Also mirror Steward golden to canonical factory_outputs path
        result = generate_product(bp, out, blocks_root=blocks_root)
        if bp.product_id == "cerebrum-steward":
            canonical = factory_outputs_root() / "Cerebrum-Steward"
            generate_product(bp, canonical, blocks_root=blocks_root)
            result["canonical_output"] = str(canonical)
        state.product_design.generation = {
            "output_dir": result["output_dir"],
            "inputs_hash": result["inputs_hash"],
            "product_id": result["product_id"],
            "canonical_output": result.get("canonical_output"),
            # Without these the client cannot tell a finished template
            # product from a runner build that has only just started, and
            # would go straight to a 409 on download.
            "engine": result.get("engine"),
            "build": result.get("build"),
        }
        state.product_design.last_error = None
        update_session(session_id, state)
        return {
            "ok": True,
            "blueprint": state.product_design.blueprint,
            "plan": state.product_design.plan,
            "generation": state.product_design.generation,
        }
    except UnsafeOutputDir as exc:
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DualRegistryError as exc:
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        state.product_design.last_error = str(exc)
        update_session(session_id, state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
