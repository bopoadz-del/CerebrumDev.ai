"""Production Steward RAG + structured pack APIs.

Trusted tenant/estate scope is resolved from server headers (or demo defaults),
never from model output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.steward.config import PLATFORM_PROJECT_ID, PLATFORM_TENANT_ID, get_config
from app.steward.db import init_engine, session_scope
from app.steward.embeddings import get_embedder, reset_embedder
from app.steward.ingestion import ingest_bytes
from app.steward.migrations_runner import run_migrations
from app.steward.models import Document, FacilityAsset, FleetVehicle, HospitalityIntent, SourceRecord
from app.steward.packs import REJECTED_SOURCES
from app.steward.retrieval import combined_layer_search, hybrid_search

router = APIRouter(tags=["steward-rag"])


class IngestRequest(BaseModel):
    filename: str
    text: str
    knowledge_layer: int = Field(2, ge=1, le=2)
    authority_kind: str = "estate_owned"
    cite_as_policy: bool = True
    rag_pack_id: Optional[str] = None


def _scope(
    x_tenant_id: Optional[str],
    x_estate_id: Optional[str],
) -> tuple[str, str]:
    # Server-trusted headers only; never accept authority from body for isolation.
    tenant = (x_tenant_id or "").strip() or "tenant_a"
    estate = (x_estate_id or "").strip() or "estate_a"
    return tenant, estate


@router.get("/v1/steward/rag/status")
def rag_status() -> Dict[str, Any]:
    cfg = get_config()
    try:
        embedder = get_embedder(cfg)
        fingerprint = embedder.fingerprint
        backend = embedder.backend_name
    except Exception as exc:  # pragma: no cover - depends on env
        fingerprint = None
        backend = f"error:{exc}"
    return {
        "ok": True,
        "embed_backend": backend,
        "fingerprint": fingerprint,
        "require_production_embeddings": cfg.require_production_embeddings,
        "platform_scope": {
            "tenant_id": PLATFORM_TENANT_ID,
            "project_id": PLATFORM_PROJECT_ID,
        },
        "rejected_sources": REJECTED_SOURCES,
    }


@router.get("/v1/steward/packs")
def pack_inventory() -> Dict[str, Any]:
    init_engine()
    with session_scope() as session:
        rows = session.execute(select(SourceRecord)).scalars().all()
        docs = session.execute(
            select(Document.rag_pack_id, func.count())
            .group_by(Document.rag_pack_id)
        ).all()
    return {
        "ok": True,
        "sources": [
            {
                "rag_pack_id": r.rag_pack_id,
                "source_id": r.source_id,
                "licence": r.licence,
                "profile": r.profile,
                "authority_kind": r.authority_kind,
                "document_count": r.document_count,
                "chunk_count": r.chunk_count,
                "record_count": r.record_count,
                "content_checksum": r.content_checksum,
            }
            for r in rows
        ],
        "documents_by_pack": {pack: count for pack, count in docs if pack},
        "rejected_sources": REJECTED_SOURCES,
    }


@router.get("/v1/steward/rag/query")
def steward_query(
    q: str = Query(..., min_length=1),
    layer: Optional[int] = Query(None, ge=1, le=2),
    top_k: int = Query(6, ge=1, le=20),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_estate_id: Optional[str] = Header(None, alias="X-Estate-Id"),
) -> Dict[str, Any]:
    tenant_id, estate_id = _scope(x_tenant_id, x_estate_id)
    init_engine()
    with session_scope() as session:
        if layer == 1:
            hits = hybrid_search(
                session,
                tenant_id=PLATFORM_TENANT_ID,
                project_id=PLATFORM_PROJECT_ID,
                query=q,
                top_k=top_k,
                knowledge_layer=1,
            )
            return {
                "ok": True,
                "query": q,
                "layer": 1,
                "hit_count": len(hits),
                "hits": [h.to_dict() for h in hits],
                "insufficiency": len(hits) == 0,
            }
        if layer == 2:
            hits = hybrid_search(
                session,
                tenant_id=tenant_id,
                project_id=estate_id,
                query=q,
                top_k=top_k,
                knowledge_layer=2,
            )
            return {
                "ok": True,
                "query": q,
                "layer": 2,
                "tenant_id": tenant_id,
                "estate_id": estate_id,
                "hit_count": len(hits),
                "hits": [h.to_dict() for h in hits],
                "insufficiency": len(hits) == 0,
            }
        return combined_layer_search(
            session,
            estate_tenant_id=tenant_id,
            estate_project_id=estate_id,
            query=q,
            top_k=top_k,
        )


@router.post("/v1/steward/rag/ingest")
def steward_ingest(
    body: IngestRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_estate_id: Optional[str] = Header(None, alias="X-Estate-Id"),
) -> Dict[str, Any]:
    tenant_id, estate_id = _scope(x_tenant_id, x_estate_id)
    if body.knowledge_layer == 1:
        tenant_id, estate_id = PLATFORM_TENANT_ID, PLATFORM_PROJECT_ID
    init_engine()
    try:
        with session_scope() as session:
            from app.steward.packs import ensure_estate_scope, ensure_platform_scope

            if body.knowledge_layer == 1:
                ensure_platform_scope(session)
            else:
                ensure_estate_scope(
                    session, tenant_id=tenant_id, estate_id=estate_id, name=estate_id
                )
            doc = ingest_bytes(
                session,
                tenant_id=tenant_id,
                project_id=estate_id,
                knowledge_layer=body.knowledge_layer,
                filename=body.filename,
                data=body.text.encode("utf-8"),
                content_type="text/plain",
                rag_pack_id=body.rag_pack_id,
                authority_kind=body.authority_kind,
                cite_as_policy=body.cite_as_policy,
            )
            return {
                "ok": True,
                "document_id": doc.id,
                "chunk_count": doc.chunk_count,
                "embedding_fingerprint": doc.embedding_fingerprint,
            }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v1/steward/facilities")
def facilities_query(
    q: Optional[str] = None,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_estate_id: Optional[str] = Header(None, alias="X-Estate-Id"),
) -> Dict[str, Any]:
    tenant_id, estate_id = _scope(x_tenant_id, x_estate_id)
    init_engine()
    with session_scope() as session:
        stmt = select(FacilityAsset).where(
            FacilityAsset.tenant_id == tenant_id,
            FacilityAsset.estate_id == estate_id,
        )
        rows = session.execute(stmt).scalars().all()
        if q:
            ql = q.lower()
            rows = [
                r
                for r in rows
                if ql in (r.asset_label or "").lower()
                or ql in (r.work_order_id or "").lower()
                or ql in (r.status or "").lower()
            ]
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "estate_id": estate_id,
        "evaluation_only": True,
        "count": len(rows),
        "assets": [
            {
                "id": r.id,
                "asset_label": r.asset_label,
                "work_order_id": r.work_order_id,
                "status": r.status,
                "manufacturer": r.manufacturer,
                "model": r.model,
                "evaluation_only": r.evaluation_only,
            }
            for r in rows
        ],
    }


@router.get("/v1/steward/fleet")
def fleet_query(
    vehicle_id: Optional[str] = None,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_estate_id: Optional[str] = Header(None, alias="X-Estate-Id"),
) -> Dict[str, Any]:
    tenant_id, estate_id = _scope(x_tenant_id, x_estate_id)
    init_engine()
    with session_scope() as session:
        stmt = select(FleetVehicle).where(
            FleetVehicle.tenant_id == tenant_id,
            FleetVehicle.estate_id == estate_id,
        )
        rows = session.execute(stmt).scalars().all()
        if vehicle_id:
            rows = [r for r in rows if r.vin_or_internal_id == vehicle_id]
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "estate_id": estate_id,
        "evaluation_only": True,
        "count": len(rows),
        "vehicles": [
            {
                "vin_or_internal_id": r.vin_or_internal_id,
                "registration": r.registration,
                "make": r.make,
                "model": r.model,
                "odometer_km": r.odometer_km,
                "next_service_km": r.next_service_km,
                "next_service_due": r.next_service_due,
                "defect_severity": r.defect_severity,
                "availability_status": r.availability_status,
                "evaluation_only": r.evaluation_only,
                "note": "Evaluation/demo row — not the estate's actual vehicle inventory",
            }
            for r in rows
        ],
    }


@router.get("/v1/steward/intents/classify")
def classify_intent(q: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Routing aid only — never policy evidence."""
    init_engine()
    ql = q.lower()
    with session_scope() as session:
        rows = session.execute(select(HospitalityIntent)).scalars().all()
    scored: List[Dict[str, Any]] = []
    for row in rows:
        score = 1.0 if any(tok in ql for tok in row.utterance.lower().split()[:4]) else 0.0
        if score:
            scored.append(
                {
                    "intent": row.intent_label,
                    "example": row.utterance,
                    "cite_as_policy": False,
                    "authority_kind": "synthetic",
                }
            )
    return {
        "ok": True,
        "query": q,
        "matches": scored[:5],
        "warning": "Intent matches are routing/evaluation only and must not be cited as policy",
    }


@router.post("/v1/steward/admin/migrate")
def admin_migrate() -> Dict[str, Any]:
    init_engine()
    run_migrations()
    return {"ok": True}


@router.post("/v1/steward/admin/reset_embedder")
def admin_reset_embedder() -> Dict[str, Any]:
    reset_embedder()
    return {"ok": True}
