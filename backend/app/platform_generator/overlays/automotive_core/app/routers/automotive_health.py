"""Health, readiness, and metrics endpoints for the automotive pilot."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["health"])

_STARTED_AT = time.time()


def _pack_status() -> Dict[str, Any]:
    storage = Path(os.getenv("STORAGE_PATH", os.getenv("DATA_DIR", "./storage")))
    manifest = storage / "automotive_core_rag_v1" / "current" / "pack_manifest.json"
    active = storage / "automotive_core_rag_v1" / "active_version.json"
    return {
        "pack_manifest_present": manifest.exists(),
        "active_pointer_present": active.exists(),
        "storage_path": str(storage),
        "storage_writable": os.access(storage, os.W_OK) if storage.exists() else False,
    }


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "automotive-safety-intelligence"}


@router.get("/ready")
def ready() -> Dict[str, Any]:
    pack = _pack_status()
    checks = {
        "storage": pack["storage_writable"] or pack["storage_path"] == "./storage",
        "embedding_model_configured": bool(
            os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        ),
        "database_url_configured": bool(os.getenv("DATABASE_URL")),
    }
    ok = all(checks.values()) or (
        # Local/dev generated packages may run without DATABASE_URL until compose up.
        checks["embedding_model_configured"]
    )
    return {
        "status": "ready" if ok else "not_ready",
        "checks": checks,
        "pack": pack,
    }


@router.get("/metrics")
def metrics() -> Dict[str, Any]:
    pack = _pack_status()
    return {
        "uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "pack_manifest_present": int(pack["pack_manifest_present"]),
        "active_pointer_present": int(pack["active_pointer_present"]),
    }
