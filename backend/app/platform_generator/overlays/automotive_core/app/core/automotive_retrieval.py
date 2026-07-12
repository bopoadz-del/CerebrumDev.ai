"""Layer-aware automotive retrieval for the foundation corpus.

This module is the narrow public interface that chat, admin and evaluation
call. It returns evidence from ``automotive_core_v1`` only; client-private
blending is wired in PR 3.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.rag.embeddings import get_embedder
from app.core.rag.vector_store import get_store

logger = logging.getLogger(__name__)

FOUNDATION_PROJECT_ID = "automotive_core_v1"

_CAMPAIGN_RE = re.compile(r"\b\d{2}[A-Z]\d{3,6}\b", re.IGNORECASE)
_YEAR_MAKE_MODEL_RE = re.compile(
    r"\b(19|20)\d{2}\b\s+(?P<make>[A-Za-z][A-Za-z0-9\-]+)\s+(?P<model>[A-Za-z][A-Za-z0-9\- ]+?)(?:\s+(recall|campaign|problem))?",
    re.IGNORECASE,
)


@dataclass
class AutomotiveEvidence:
    """One retrieved result with full citation metadata."""

    knowledge_layer: str
    foundation_pack_id: str
    source_family: str
    source_title: str
    source_authority: str
    source_url: Optional[str]
    record_reference: str
    retrieval_score: float
    chunk_text: str
    metadata: Dict[str, Any]


def _extract_campaign_number(query: str) -> Optional[str]:
    match = _CAMPAIGN_RE.search(query)
    if match:
        return match.group(0).upper()
    return None


def _extract_year_make_model(query: str) -> Optional[Dict[str, str]]:
    match = _YEAR_MAKE_MODEL_RE.search(query)
    if match:
        return {
            "year": match.group(1) + match.group(2),
            "make": match.group("make").strip().lower(),
            "model": match.group("model").strip().lower(),
        }
    return None


def retrieve_foundation_evidence(
    query: str,
    top_k: int = 5,
) -> List[AutomotiveEvidence]:
    """Retrieve evidence from the automotive foundation corpus.

    Strategy:
      1. If a campaign number is present, boost exact identifier matches.
      2. Embed the query and run semantic + BM25 hybrid search.
      3. Return ranked evidence with citation metadata.
    """
    if not query or not query.strip():
        return []

    embedder = get_embedder()
    store = get_store(dim=embedder.dim)
    query_vec = embedder.encode_queries([query])[0]

    # Identifier-aware exact campaign lookup.
    campaign = _extract_campaign_number(query)
    identifier_candidates = []
    if campaign:
        identifier_candidates = store.identifier_search(
            FOUNDATION_PROJECT_ID, [campaign], k=max(top_k * 4, 20)
        )

    # Hybrid semantic + lexical search.
    semantic_candidates = store.search(
        FOUNDATION_PROJECT_ID, query_vec, k=max(top_k * 4, 20), query_text=query
    )

    # Fuse by chunk_id, preferring identifier matches when present.
    by_id: Dict[str, Any] = {}
    for c in semantic_candidates:
        by_id[c.chunk_id] = {"chunk": c, "score": c.score or 0.0, "id_boost": 0.0}
    for c in identifier_candidates:
        entry = by_id.get(c.chunk_id)
        if entry:
            entry["id_boost"] = 2.0
        else:
            by_id[c.chunk_id] = {"chunk": c, "score": 0.0, "id_boost": 2.0}

    scored = [
        (entry["score"] + entry["id_boost"], entry["chunk"])
        for entry in by_id.values()
    ]
    scored.sort(key=lambda x: -x[0])

    results: List[AutomotiveEvidence] = []
    for score, chunk in scored[:top_k]:
        results.append(
            AutomotiveEvidence(
                knowledge_layer=FOUNDATION_PROJECT_ID,
                foundation_pack_id="automotive_core_rag_v1",
                source_family="recall",
                source_title=f"NHTSA Recall {chunk.doc_id.split(':')[-1]}",
                source_authority="primary",
                source_url=None,
                record_reference=chunk.doc_id.split(":")[-1],
                retrieval_score=round(float(score), 6),
                chunk_text=chunk.text,
                metadata={
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "chunk_index": chunk.chunk_index,
                },
            )
        )
    return results


def retrieve_by_campaign_number(campaign_number: str) -> List[AutomotiveEvidence]:
    """Exact campaign-number lookup."""
    return retrieve_foundation_evidence(
        query=f"recall campaign {campaign_number}",
        top_k=5,
    )
