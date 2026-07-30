"""Hybrid retrieval: pgvector semantic + Postgres lexical + RRF fusion.

Trusted tenant_id + project_id (estate) filters are mandatory and applied
before ranking/fusion. Layer-1 platform retrieval uses the governed shared
project ``prebuilt_steward_core``. Synthetic/intent rows are never returned
as policy citations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.steward.config import (
    PLATFORM_PROJECT_ID,
    PLATFORM_TENANT_ID,
    StewardRagConfig,
    get_config,
)
from app.steward.embeddings import get_embedder
from app.steward.models import DocumentChunk


@dataclass
class Citation:
    chunk_id: str
    document_id: str
    tenant_id: str
    project_id: str
    knowledge_layer: int
    filename: Optional[str]
    page: Optional[int]
    sheet: Optional[str]
    row_start: Optional[int]
    row_end: Optional[int]
    chunk_ordinal: int
    excerpt: str
    vector_score: Optional[float] = None
    lexical_score: Optional[float] = None
    fused_score: float = 0.0
    fused_rank: int = 0
    rag_pack_id: Optional[str] = None
    authority_kind: str = "estate_owned"
    cite_as_policy: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _semantic(
    session: Session,
    tenant_id: str,
    project_id: str,
    query: str,
    limit: int,
    *,
    knowledge_layer: Optional[int] = None,
) -> List[Dict[str, Any]]:
    embedder = get_embedder()
    qvec = embedder.embed(query)
    distance = DocumentChunk.embedding.cosine_distance(qvec).label("distance")
    filters = [
        DocumentChunk.tenant_id == tenant_id,
        DocumentChunk.project_id == project_id,
        DocumentChunk.cite_as_policy.is_(True),
    ]
    if knowledge_layer is not None:
        filters.append(DocumentChunk.knowledge_layer == knowledge_layer)
    stmt = (
        select(DocumentChunk, distance)
        .where(*filters)
        .order_by(distance)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [{"chunk": chunk, "score": 1.0 - float(dist)} for chunk, dist in rows]


def _lexical(
    session: Session,
    tenant_id: str,
    project_id: str,
    query: str,
    limit: int,
    *,
    knowledge_layer: Optional[int] = None,
) -> List[Dict[str, Any]]:
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(DocumentChunk.text_tsv, tsquery).label("rank")
    filters = [
        DocumentChunk.tenant_id == tenant_id,
        DocumentChunk.project_id == project_id,
        DocumentChunk.cite_as_policy.is_(True),
        DocumentChunk.text_tsv.op("@@")(tsquery),
    ]
    if knowledge_layer is not None:
        filters.append(DocumentChunk.knowledge_layer == knowledge_layer)
    stmt = (
        select(DocumentChunk, rank)
        .where(*filters)
        .order_by(rank.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [{"chunk": chunk, "score": float(r)} for chunk, r in rows]


def reciprocal_rank_fusion(
    ranked_lists: List[List[Dict[str, Any]]],
    k: int,
) -> Dict[str, Dict[str, Any]]:
    fused: Dict[str, Dict[str, Any]] = {}
    for results in ranked_lists:
        for rank, item in enumerate(results, start=1):
            chunk = item["chunk"]
            entry = fused.setdefault(
                chunk.id,
                {"chunk": chunk, "fused": 0.0, "vector": None, "lexical": None},
            )
            entry["fused"] += 1.0 / (k + rank)
    return fused


def hybrid_search(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    query: str,
    config: Optional[StewardRagConfig] = None,
    top_k: Optional[int] = None,
    knowledge_layer: Optional[int] = None,
) -> List[Citation]:
    if not tenant_id or not project_id:
        raise ValueError("tenant_id and project_id are required for retrieval")
    config = config or get_config()
    top_k = top_k or config.top_k

    semantic = _semantic(
        session,
        tenant_id,
        project_id,
        query,
        config.vector_candidates,
        knowledge_layer=knowledge_layer,
    )
    lexical = _lexical(
        session,
        tenant_id,
        project_id,
        query,
        config.lexical_candidates,
        knowledge_layer=knowledge_layer,
    )
    fused = reciprocal_rank_fusion([semantic, lexical], config.rrf_k)
    for item in semantic:
        fused[item["chunk"].id]["vector"] = item["score"]
    for item in lexical:
        fused[item["chunk"].id]["lexical"] = item["score"]

    ordered = sorted(fused.values(), key=lambda e: e["fused"], reverse=True)[:top_k]
    citations: List[Citation] = []
    for rank, entry in enumerate(ordered, start=1):
        chunk: DocumentChunk = entry["chunk"]
        citations.append(
            Citation(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                tenant_id=chunk.tenant_id,
                project_id=chunk.project_id,
                knowledge_layer=chunk.knowledge_layer,
                filename=chunk.source_filename,
                page=chunk.page,
                sheet=chunk.sheet,
                row_start=chunk.row_start,
                row_end=chunk.row_end,
                chunk_ordinal=chunk.chunk_ordinal,
                excerpt=(chunk.text or "")[:400],
                vector_score=entry["vector"],
                lexical_score=entry["lexical"],
                fused_score=round(entry["fused"], 6),
                fused_rank=rank,
                rag_pack_id=chunk.rag_pack_id,
                authority_kind=chunk.authority_kind,
                cite_as_policy=bool(chunk.cite_as_policy),
                metadata=chunk.meta or {},
            )
        )
    return citations


def combined_layer_search(
    session: Session,
    *,
    estate_tenant_id: str,
    estate_project_id: str,
    query: str,
    config: Optional[StewardRagConfig] = None,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """Layer 2 estate first, then Layer 1 platform — explicit layer reporting."""
    config = config or get_config()
    top_k = top_k or config.top_k
    layer2 = hybrid_search(
        session,
        tenant_id=estate_tenant_id,
        project_id=estate_project_id,
        query=query,
        config=config,
        top_k=top_k,
        knowledge_layer=2,
    )
    layer1 = hybrid_search(
        session,
        tenant_id=PLATFORM_TENANT_ID,
        project_id=PLATFORM_PROJECT_ID,
        query=query,
        config=config,
        top_k=top_k,
        knowledge_layer=1,
    )
    # Prefer estate overrides: keep layer2 first, then fill from layer1.
    merged: List[Citation] = list(layer2)
    seen = {c.chunk_id for c in merged}
    for c in layer1:
        if c.chunk_id not in seen and len(merged) < top_k:
            merged.append(c)
            seen.add(c.chunk_id)
    return {
        "ok": True,
        "query": query,
        "layers": {
            "1": [c.to_dict() for c in layer1],
            "2": [c.to_dict() for c in layer2],
        },
        "hits": [c.to_dict() for c in merged],
        "hit_count": len(merged),
        "insufficiency": len(merged) == 0,
        # Source precedence, disclosed: when both layers answer, estate
        # documents (layer 2) win over platform documents (layer 1).
        "precedence": {
            "rule": "estate_overrides_platform",
            "layer2_hits": len(layer2),
            "layer1_hits": len(layer1),
            "conflict_possible": bool(layer1 and layer2),
        },
    }
