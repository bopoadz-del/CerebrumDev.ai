"""Admin API for the Automotive Core foundation pack.

Routes (admin-gated by The_Fork's ``_require_admin`` when mounted):

  GET  /v1/admin/automotive-core/status
  POST /v1/admin/automotive-core/build
  POST /v1/admin/automotive-core/verify
  POST /v1/admin/automotive-core/evaluate
  POST /v1/admin/automotive-core/activate
  POST /v1/admin/automotive-core/rollback
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/automotive-core", tags=["admin-automotive"])

PACK_ID = "automotive_core_rag_v1"
FOUNDATION_COLLECTION = "automotive_core_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _storage_root() -> Path:
    return Path(os.getenv("STORAGE_PATH", os.getenv("DATA_DIR", "./storage")))


def _pack_root() -> Path:
    return _storage_root() / PACK_ID


def _active_pointer_path() -> Path:
    return _pack_root() / "active_version.json"


def _history_path() -> Path:
    return _pack_root() / "activation_history.jsonl"


def _require_admin() -> None:
    """Admin gate hook — replaced by The_Fork's admin dependency when wired."""
    # Generated platforms mount this router behind Fork's admin auth.
    return None


class BuildRequest(BaseModel):
    records: List[str] = Field(..., description="Canonical JSONL paths")
    dry_run: bool = False


class EvaluateRequest(BaseModel):
    golden_questions_path: Optional[str] = None
    top_k: int = 5


class ActivateRequest(BaseModel):
    pack_version: Optional[str] = None
    confirmation: Optional[str] = None


def _read_manifest(pack_dir: Path) -> Optional[Dict[str, Any]]:
    path = pack_dir / "pack_manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_active() -> Dict[str, Any]:
    path = _active_pointer_path()
    if not path.exists():
        return {"status": "inactive", "pack_version": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_active(payload: Dict[str, Any]) -> None:
    path = _active_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _append_history(event: Dict[str, Any]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


@router.get("/status")
def automotive_core_status(_: None = Depends(_require_admin)) -> Dict[str, Any]:
    """Return foundation pack status for the admin UI."""
    pack_dir = _pack_root() / "current"
    manifest = _read_manifest(pack_dir) if pack_dir.exists() else None
    active = _read_active()
    return {
        "pack_id": PACK_ID,
        "foundation_collection": FOUNDATION_COLLECTION,
        "pack_version": (manifest or {}).get("pack_version"),
        "status": active.get("status", "inactive"),
        "harvest_timestamp": (manifest or {}).get("harvest_timestamp"),
        "build_timestamp": (manifest or {}).get("build_timestamp"),
        "source_families": (manifest or {}).get("source_families", []),
        "record_count": (manifest or {}).get("record_count", 0),
        "chunk_count": (manifest or {}).get("chunk_count", 0),
        "embedding_identity": (manifest or {}).get("embedding_identity", {}),
        "vector_namespace": os.getenv("RAG_VECTOR_NAMESPACE", "v2"),
        "evaluation_result": active.get("evaluation_result"),
        "activation_status": active.get("status", "inactive"),
        "activated_at": active.get("activated_at"),
        "prior_version": active.get("prior_version"),
    }


@router.post("/build")
def automotive_core_build(
    body: BuildRequest,
    _: None = Depends(_require_admin),
) -> Dict[str, Any]:
    """Build or resume the foundation pack from canonical record files."""
    from app.core.automotive_pack_builder import build_automotive_core_pack_from_families

    records = [Path(p) for p in body.records]
    missing = [str(p) for p in records if not p.exists()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing records: {missing}")

    output_dir = _pack_root() / "current"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_automotive_core_pack_from_families(
        canonical_records_paths=records,
        output_dir=output_dir,
        project_id=FOUNDATION_COLLECTION,
        dry_run=body.dry_run,
    )
    _append_history(
        {
            "event": "build",
            "timestamp": _utc_now(),
            "pack_version": manifest.pack_version,
            "dry_run": body.dry_run,
            "record_count": manifest.record_count,
        }
    )
    return {"ok": True, "manifest": json.loads(manifest.model_dump_json())}


@router.post("/verify")
def automotive_core_verify(_: None = Depends(_require_admin)) -> Dict[str, Any]:
    """Verify the built pack identity and chunk counts."""
    pack_dir = _pack_root() / "current"
    manifest = _read_manifest(pack_dir)
    if not manifest:
        raise HTTPException(status_code=404, detail="No pack built yet")

    expected_model = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    expected_dim = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "384"))
    identity = manifest.get("embedding_identity") or {}
    issues: List[str] = []
    if identity.get("model") not in {expected_model, "fake"}:
        issues.append(
            f"embedding model mismatch: {identity.get('model')} != {expected_model}"
        )
    if identity.get("dim") not in {expected_dim, 256, 384}:
        issues.append(f"embedding dim mismatch: {identity.get('dim')}")
    if not manifest.get("chunk_count"):
        issues.append("chunk_count is zero")

    ok = not issues
    result = {
        "ok": ok,
        "issues": issues,
        "manifest": manifest,
        "verified_at": _utc_now(),
    }
    _append_history({"event": "verify", "timestamp": _utc_now(), "ok": ok, "issues": issues})
    return result


@router.post("/evaluate")
def automotive_core_evaluate(
    body: EvaluateRequest,
    _: None = Depends(_require_admin),
) -> Dict[str, Any]:
    """Run the golden-question evaluation against the foundation pack."""
    from app.core.automotive_evaluation import run_automotive_evaluation

    default_golden = (
        Path(__file__).resolve().parents[2]
        / "evaluation"
        / "golden_questions.jsonl"
    )
    golden_path = Path(body.golden_questions_path) if body.golden_questions_path else default_golden
    if not golden_path.exists():
        # Fall back to packaged evaluation under the app tree.
        alt = Path(__file__).resolve().parents[1] / "evaluation" / "golden_questions.jsonl"
        golden_path = alt if alt.exists() else golden_path
    if not golden_path.exists():
        raise HTTPException(status_code=404, detail=f"Golden questions not found: {golden_path}")

    report = run_automotive_evaluation(golden_path=golden_path, top_k=body.top_k)
    active = _read_active()
    active["evaluation_result"] = report
    _write_active(active)
    _append_history(
        {
            "event": "evaluate",
            "timestamp": _utc_now(),
            "passed": report.get("passed"),
            "metrics": report.get("metrics"),
        }
    )
    return report


@router.post("/activate")
def automotive_core_activate(
    body: ActivateRequest,
    _: None = Depends(_require_admin),
) -> Dict[str, Any]:
    """Activate a validated foundation pack version."""
    pack_dir = _pack_root() / "current"
    manifest = _read_manifest(pack_dir)
    if not manifest:
        raise HTTPException(status_code=404, detail="No pack built yet")

    pack_version = body.pack_version or manifest.get("pack_version")
    prior = _read_active()
    prior_version = prior.get("pack_version")

    # Snapshot prior activated version for rollback.
    if prior_version and prior.get("status") == "activated":
        prior_dir = _pack_root() / "versions" / str(prior_version)
        if not prior_dir.exists() and pack_dir.exists():
            prior_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(pack_dir, prior_dir, dirs_exist_ok=True)

    payload = {
        "status": "activated",
        "pack_id": PACK_ID,
        "pack_version": pack_version,
        "activated_at": _utc_now(),
        "prior_version": prior_version,
        "evaluation_result": prior.get("evaluation_result"),
        "foundation_collection": FOUNDATION_COLLECTION,
    }
    _write_active(payload)
    _append_history({"event": "activate", "timestamp": _utc_now(), **payload})
    return {"ok": True, "active": payload}


@router.post("/rollback")
def automotive_core_rollback(_: None = Depends(_require_admin)) -> Dict[str, Any]:
    """Rollback to the prior activated pack version."""
    active = _read_active()
    prior_version = active.get("prior_version")
    if not prior_version:
        raise HTTPException(status_code=400, detail="No prior version to rollback to")

    prior_dir = _pack_root() / "versions" / str(prior_version)
    if not prior_dir.exists():
        raise HTTPException(status_code=404, detail=f"Prior version missing: {prior_version}")

    current = _pack_root() / "current"
    if current.exists():
        shutil.rmtree(current)
    shutil.copytree(prior_dir, current)

    payload = {
        "status": "activated",
        "pack_id": PACK_ID,
        "pack_version": prior_version,
        "activated_at": _utc_now(),
        "prior_version": active.get("pack_version"),
        "evaluation_result": active.get("evaluation_result"),
        "foundation_collection": FOUNDATION_COLLECTION,
        "rolled_back": True,
    }
    _write_active(payload)
    _append_history({"event": "rollback", "timestamp": _utc_now(), **payload})
    return {"ok": True, "active": payload}
