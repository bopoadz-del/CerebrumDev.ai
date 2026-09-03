"""Session-scoped product architecture API (Design Product mode).

POST /v1/sessions/{id}/product/draft
POST /v1/sessions/{id}/product/plan
POST /v1/sessions/{id}/product/approve
POST /v1/sessions/{id}/product/generate
POST /v1/sessions/{id}/product/pilot
GET  /v1/sessions/{id}/product
GET  /v1/sessions/{id}/product/package   (export the generated platform zip)
POST /v1/sessions/{id}/product/mode
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from app.core.auth import Principal, require_api_key
from app.core.billing import require_entitled
from app.core.llm_throttle import require_llm_rate
from app.core.session_guard import owned_session_or_404
from app.core.session_store import update_session
from app.core.trial_limits import require_remaining, require_within_limit

logger = logging.getLogger(__name__)
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
    #: ``pilot`` reopens TESTER/STORE on the existing workspace (same hash).
    cycle: Optional[Literal["code", "pilot"]] = None


class ModeBody(BaseModel):
    mode: Literal["kit", "product"]


def _require_session(session_id: str, principal: Principal):
    """Same ownership rule as every other session-scoped router."""
    return owned_session_or_404(session_id, principal)


def _session_output(session_id: str, product_id: str) -> Path:
    return factory_outputs_root() / "sessions" / session_id / product_id


def _enforce_export_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: exports are metered per account."""
    require_within_limit(account_id, "export")


def _enforce_generation_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: generations are metered per account."""
    require_within_limit(account_id, "generation")


def _raise_product_error(session_id: str, state, exc: BaseException) -> None:
    """400 for blueprint/registry mistakes; 500 (and Sentry) for the rest."""
    state.product_design.last_error = str(exc)
    update_session(session_id, state)
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(
        exc, (BlueprintError, DualRegistryError, UnsafeOutputDir, ValidationError)
    ):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.exception("session product handler failed")
    raise HTTPException(status_code=500, detail="internal_error") from exc


def _consume_generation_on_start(account_id: Optional[str]) -> None:
    """Charge one generation after a build actually started."""
    require_within_limit(account_id, "generation")


_EXPORT_SKIP_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    ".git",
}
_EXPORT_SKIP_SUFFIXES = {".pyc", ".pyo"}
# ZIP spec rejects DOS timestamps before 1980. Copied kit/kernel files in
# this environment (and some git checkouts) have mtime 0.
_ZIP_MIN_EPOCH = 315532800  # 1980-01-01 UTC


_PROTOTYPE_MARKER = "CODE_CYCLE_PROTOTYPE.txt"
_PROTOTYPE_MARKER_BODY = (
    "This export is a CODE-CYCLE PROTOTYPE, not a pilot-ready product.\n"
    "\n"
    "The factory recorded RUN_SUCCEEDED with cycle=code and pilot_ready=false.\n"
    "PRODUCT (pytest -m pilot) and STORE ops have not passed. Dual certification\n"
    "is pending. Frontend modules and some handlers may be Factory templates.\n"
    "\n"
    "Say continue on the Factory Floor (or POST /product/pilot) to open a\n"
    "Store-green cycle on the same workspace. Do not treat this zip as a\n"
    "finished production platform.\n"
)


def _maybe_write_prototype_marker(zf: zipfile.ZipFile, out: Path) -> None:
    """Stamp a code-cycle zip so the export cannot be mistaken for finished."""
    try:
        from app.factory.build.ledger import BuildLedger
        from app.factory.build_jobs import _ledger_path

        ledger = BuildLedger(_ledger_path(out))
        if not ledger.exists() or ledger.pilot_ready():
            return
        if not ledger.succeeded():
            return
    except Exception:  # noqa: BLE001
        return
    if _PROTOTYPE_MARKER in zf.namelist():
        return
    import time as _time

    info = zipfile.ZipInfo(_PROTOTYPE_MARKER, date_time=_time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, _PROTOTYPE_MARKER_BODY)


def zip_generated_product(out: Path, archive_base: Path) -> Path:
    """Zip ``out`` to ``{archive_base}.zip``, omitting TESTER caches.

    ``shutil.make_archive`` copies the whole tree, including ``__pycache__``
    and ``.pytest_cache`` left by the factory's own pytest run. Those are
    not the product; the live winery-hospitality export shipped 146 files
    of which a large slice was bytecode.
    """
    import time as _time

    zip_path = Path(str(archive_base) + ".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _maybe_write_prototype_marker(zf, out)
        for path in sorted(out.rglob("*")):
            rel = path.relative_to(out)
            if any(part in _EXPORT_SKIP_DIR_NAMES for part in rel.parts):
                continue
            if path.suffix in _EXPORT_SKIP_SUFFIXES:
                continue
            if not path.is_file():
                continue
            mtime = max(path.stat().st_mtime, _ZIP_MIN_EPOCH)
            info = zipfile.ZipInfo(rel.as_posix(), date_time=_time.localtime(mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    return zip_path


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
    if status["state"] == "stalled":
        raise HTTPException(
            status_code=409,
            detail=(
                "the build stalled and will not be shipped as a full-pilot zip: "
                + str(status.get("detail"))
            ),
        )

    archive_base = out.parent / f"{out.name}-export"
    archive = zip_generated_product(out, archive_base)
    product_id = gen.get("product_id") or out.name
    filename = (
        f"cerebrumdev-{product_id}-code-cycle-prototype.zip"
        if status.get("state") == "succeeded" and status.get("pilot_ready") is False
        else f"cerebrumdev-{product_id}.zip"
    )
    return FileResponse(
        archive,
        filename=filename,
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
    session_id: str, body: DraftBody, principal: Principal = Depends(require_entitled)
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
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)


@router.post("/{session_id}/product/plan")
def plan_product(
    session_id: str, principal: Principal = Depends(require_entitled)
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
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)


@router.post("/{session_id}/product/approve")
def approve_blueprint(
    session_id: str, body: ApproveBody, principal: Principal = Depends(require_entitled)
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


@router.post("/{session_id}/product/pilot")
def run_pilot_cycle(
    session_id: str,
    principal: Principal = Depends(require_entitled),
) -> Dict[str, Any]:
    """Reopen TESTER/STORE on the existing workspace for Store-green.

    Same session, hash, and output dir. Does not approve a new blueprint.
    """
    from app.factory.platform_chat_flow import resume_pilot_cycle

    state = _require_session(session_id, principal)
    require_remaining(principal.account_id, "generation")
    require_llm_rate(principal, "generate")
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    try:
        result = resume_pilot_cycle(state, triggered_by="product_pilot")
        if not result.get("already_running") and not result.get("already_complete"):
            _consume_generation_on_start(principal.account_id)
        update_session(session_id, state)
        return {
            "ok": True,
            "cycle": "pilot",
            "generation": state.product_design.generation,
            "summary": result.get("summary"),
            "already_complete": result.get("already_complete"),
            "already_running": result.get("already_running"),
            "pilot_ready": result.get("pilot_ready"),
            "resumed": result.get("resumed"),
        }
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)


@router.post("/{session_id}/product/generate")
def generate_approved_product(
    session_id: str,
    body: Optional[GenerateBody] = None,
    principal: Principal = Depends(require_entitled),
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    require_remaining(principal.account_id, "generation")
    require_llm_rate(principal, "generate")
    body = body or GenerateBody()
    if not state.product_design.blueprint_approved:
        raise HTTPException(status_code=400, detail="approve blueprint before generate")
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    from app.factory.platform_chat_flow import has_running_build

    if has_running_build(state):
        raise HTTPException(
            status_code=409,
            detail="a build is already in progress — poll /product/build-status",
        )
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
        result = generate_product(
            bp,
            out,
            blocks_root=blocks_root,
            cycle=body.cycle,
            quota_account_id=principal.account_id,
        )
        if result.get("already_running"):
            raise HTTPException(
                status_code=409,
                detail="a build is already in progress — poll /product/build-status",
            )
        _consume_generation_on_start(principal.account_id)
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
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)
