"""Hybrid retrieval: pgvector semantic + Postgres lexical + RRF fusion.

Tenant and project filtering are *mandatory* and applied before fusion — a
query can only ever see chunks belonging to its own tenant+project. Lexical
ranking uses Postgres full-text ``ts_rank_cd`` over a generated ``tsvector``
(a documented BM25-equivalent). Results carry full citation metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.retailops.config import RetailOpsConfig, get_config
from app.retailops.embeddings import get_embedder
from app.retailops.models import DocumentChunk


@dataclass
class Citation:
    chunk_id: str
    document_id: str
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
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _semantic(
    session: Session,
    tenant_id: str,
    project_id: str,
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    embedder = get_embedder()
    qvec = embedder.embed(query)
    distance = DocumentChunk.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(DocumentChunk, distance)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.project_id == project_id,
        )
        .order_by(distance)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    out = []
    for chunk, dist in rows:
        # cosine similarity in [ -1, 1 ]; distance = 1 - similarity.
        out.append({"chunk": chunk, "score": 1.0 - float(dist)})
    return out


def _lexical(
    session: Session,
    tenant_id: str,
    project_id: str,
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(DocumentChunk.text_tsv, tsquery).label("rank")
    stmt = (
        select(DocumentChunk, rank)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.project_id == project_id,
            DocumentChunk.text_tsv.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [{"chunk": chunk, "score": float(r)} for chunk, r in rows]


def _reciprocal_rank_fusion(
    ranked_lists: List[List[Dict[str, Any]]],
    k: int,
) -> Dict[str, Dict[str, Any]]:
    """Combine ranked lists via RRF: score = Σ 1/(k + rank)."""
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
    config: Optional[RetailOpsConfig] = None,
    top_k: Optional[int] = None,
) -> List[Citation]:
    """Run semantic + lexical retrieval and fuse with RRF. Tenant+project scoped."""
    if not tenant_id or not project_id:
        raise ValueError("tenant_id and project_id are required for retrieval")
    config = config or get_config()
    top_k = top_k or config.top_k

    semantic = _semantic(session, tenant_id, project_id, query, config.vector_candidates)
    lexical = _lexical(session, tenant_id, project_id, query, config.lexical_candidates)

    fused = _reciprocal_rank_fusion([semantic, lexical], config.rrf_k)
    # attach raw component scores for transparency
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
                metadata=chunk.meta or {},
            )
        )
    return citations
