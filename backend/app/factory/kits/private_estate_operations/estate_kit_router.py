"""Estate demo fixtures + dual RAG ingest/query (Factory-generated)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.estate_kit.rag.service import get_rag_service

router = APIRouter(tags=["estate-kit"])


def _fixtures() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "data" / "demo" / "estate_fixtures.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="demo fixtures missing")
    return json.loads(path.read_text(encoding="utf-8"))


def _dual_rag_meta() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "docs" / "rag" / "dual_rag.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


class RagIngestRequest(BaseModel):
    layer: int = Field(..., ge=1, le=2)
    doc_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    property_id: Optional[str] = None
    version: Optional[str] = None


@router.get("/v1/estate/demo")
def estate_demo() -> Dict[str, Any]:
    data = _fixtures()
    return {
        "ok": True,
        "fixture_id": data.get("fixture_id"),
        "properties": data.get("properties") or [],
        "vendors": data.get("vendors") or [],
        "staff": data.get("staff") or [],
        "maintenance_calendar": data.get("maintenance_calendar") or [],
    }


@router.get("/v1/estate/properties")
def list_properties() -> Dict[str, Any]:
    props = _fixtures().get("properties") or []
    return {"count": len(props), "properties": props}


@router.get("/v1/estate/vendors")
def list_vendors() -> Dict[str, Any]:
    vendors = _fixtures().get("vendors") or []
    return {"count": len(vendors), "vendors": vendors}


@router.get("/v1/estate/staff")
def list_staff() -> Dict[str, Any]:
    staff = _fixtures().get("staff") or []
    return {"count": len(staff), "staff": staff}


@router.get("/v1/estate/maintenance")
def maintenance_calendar() -> Dict[str, Any]:
    cal = _fixtures().get("maintenance_calendar") or []
    return {"count": len(cal), "work_orders": cal}


@router.get("/v1/rag/dual")
def dual_rag_status() -> Dict[str, Any]:
    service = get_rag_service()
    try:
        service.ensure_bootstrapped()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    runtime = service.runtime_status()
    indices = [
        {
            "index_id": stat.index_id,
            "layer": stat.layer,
            "record_count": stat.record_count,
            "document_count": stat.document_count,
        }
        for stat in service.index_stats()
    ]
    meta = _dual_rag_meta()
    # Live honesty: surface the actually-serving provider, not just the template claim.
    meta = {
        **meta,
        "embedding_provider": runtime["embedding_provider"],
        "vector_adapter": runtime["persistence_adapter"],
        "active_path": "persistent_postgres"
        if runtime["persistent"]
        else "ephemeral_jsonl",
    }
    return {
        "ok": True,
        "config": meta,
        "runtime": runtime,
        "embedding_provider": runtime["embedding_provider"],
        "indices": indices,
    }


@router.post("/v1/rag/ingest")
def dual_rag_ingest(body: RagIngestRequest) -> Dict[str, Any]:
    service = get_rag_service()
    try:
        result = service.ingest_document(
            layer=body.layer,
            doc_id=body.doc_id,
            title=body.title,
            text=body.text,
            property_id=body.property_id,
            version=body.version,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/v1/rag/bootstrap")
def dual_rag_bootstrap() -> Dict[str, Any]:
    """Re-ingest demo fixtures into empty or refreshed indices."""
    service = get_rag_service()
    try:
        service.config.validate_or_raise()
        return service.bootstrap_from_fixtures()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/v1/rag/query")
def dual_rag_query(
    q: str = Query(..., min_length=1),
    layer: Optional[int] = Query(None, ge=1, le=2),
    top_k: int = Query(5, ge=1, le=20),
    min_score: float = Query(0.05, ge=0.0, le=1.0),
    property_id: Optional[str] = Query(None, min_length=1),
) -> Dict[str, Any]:
    service = get_rag_service()
    try:
        result = service.query(
            q, layer=layer, top_k=top_k, min_score=min_score, property_id=property_id
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = _dual_rag_meta()
    meta = {
        **meta,
        "embedding_provider": result.get("embedding_provider"),
        "vector_adapter": result.get("persistence_adapter"),
    }
    result["config"] = meta
    return result
