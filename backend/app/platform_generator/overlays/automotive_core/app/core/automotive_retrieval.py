"""Layer-aware automotive retrieval for the foundation corpus.

This module is the narrow public interface that chat, admin and evaluation
call. It returns evidence from ``automotive_core_v1`` by default and can
optionally blend project-scoped ``client_private`` evidence (PR 3).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from app.core.rag.embeddings import get_embedder
from app.core.rag.vector_store import get_store

logger = logging.getLogger(__name__)

FOUNDATION_PROJECT_ID = "automotive_core_v1"
CLIENT_PRIVATE_LAYER = "client_private"

_CAMPAIGN_RE = re.compile(r"\b\d{2}[A-Z]\d{3,6}\b", re.IGNORECASE)
_INVESTIGATION_RE = re.compile(r"\b(?:PE|EA|RQ|DP|SQ)\d{2,3}-\d{3,4}\b", re.IGNORECASE)
_YEAR_MAKE_MODEL_RE = re.compile(
    r"\b(19|20)\d{2}\b\s+(?P<make>[A-Za-z][A-Za-z0-9\-]+)\s+(?P<model>[A-Za-z][A-Za-z0-9\- ]+?)(?:\s+(recall|campaign|problem|complaint|rating))?",
    re.IGNORECASE,
)

KnowledgeLayer = Literal["automotive_core_v1", "client_private"]


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
    project_id: Optional[str] = None
    citation_label: str = "Automotive Core"


@dataclass
class RetrievalRequest:
    """Request envelope for layer-aware retrieval."""

    query: str
    knowledge_layers: List[KnowledgeLayer] = field(
        default_factory=lambda: ["automotive_core_v1"]
    )
    project_id: Optional[str] = None
    top_k: int = 5


def _extract_campaign_number(query: str) -> Optional[str]:
    match = _CAMPAIGN_RE.search(query)
    if match:
        return match.group(0).upper()
    return None


def _extract_investigation_number(query: str) -> Optional[str]:
    match = _INVESTIGATION_RE.search(query)
    if match:
        return match.group(0).upper()
    return None


def _extract_year_make_model(query: str) -> Optional[Dict[str, str]]:
    match = _YEAR_MAKE_MODEL_RE.search(query)
    if match:
        return {
            "year": match.group(0).split()[0],
            "make": match.group("make").strip().lower(),
            "model": match.group("model").strip().lower(),
        }
    return None


def _family_from_doc_id(doc_id: str) -> str:
    if ":" in doc_id:
        prefix = doc_id.split(":", 1)[0]
        if prefix in {"recall", "investigation", "complaint", "safety_rating"}:
            return prefix
    return "recall"


def _title_for_family(family: str, record_reference: str) -> str:
    labels = {
        "recall": f"NHTSA Recall {record_reference}",
        "investigation": f"NHTSA Investigation {record_reference}",
        "complaint": f"NHTSA Complaint {record_reference}",
        "safety_rating": f"NHTSA Safety Rating {record_reference}",
    }
    return labels.get(family, f"NHTSA Record {record_reference}")


def _citation_label(layer: str) -> str:
    if layer == CLIENT_PRIVATE_LAYER:
        return "Private Project"
    return "Automotive Core"


def _record_reference_from_chunk(chunk: Any, family: str) -> str:
    text = getattr(chunk, "text", "") or ""
    if family == "recall":
        return _extract_campaign_number(text) or str(chunk.doc_id).split(":")[-1]
    if family == "investigation":
        return _extract_investigation_number(text) or str(chunk.doc_id).split(":")[-1]
    if text.startswith("Complaint:"):
        return text.split("\n", 1)[0].replace("Complaint:", "").strip() or str(
            chunk.doc_id
        ).split(":")[-1]
    if text.startswith("VehicleId:"):
        return text.split("\n", 1)[0].replace("VehicleId:", "").strip() or str(
            chunk.doc_id
        ).split(":")[-1]
    return str(chunk.doc_id).split(":")[-1]


def _search_project(
    project_id: str,
    query: str,
    top_k: int,
    identifier_tokens: List[str],
) -> List[tuple[float, Any]]:
    embedder = get_embedder()
    store = get_store(dim=embedder.dim)
    query_vec = embedder.encode_queries([query])[0]

    identifier_candidates = []
    if identifier_tokens:
        identifier_candidates = store.identifier_search(
            project_id, identifier_tokens, k=max(top_k * 4, 20)
        )

    semantic_candidates = store.search(
        project_id, query_vec, k=max(top_k * 4, 20), query_text=query
    )

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
        (entry["score"] + entry["id_boost"], entry["chunk"]) for entry in by_id.values()
    ]
    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]


def _to_evidence(
    score: float,
    chunk: Any,
    *,
    knowledge_layer: str,
    project_id: Optional[str] = None,
) -> AutomotiveEvidence:
    family = _family_from_doc_id(getattr(chunk, "doc_id", "") or "")
    record_reference = _record_reference_from_chunk(chunk, family)
    return AutomotiveEvidence(
        knowledge_layer=knowledge_layer,
        foundation_pack_id="automotive_core_rag_v1",
        source_family=family,
        source_title=_title_for_family(family, record_reference),
        source_authority="primary",
        source_url=None,
        record_reference=record_reference,
        retrieval_score=round(float(score), 6),
        chunk_text=chunk.text,
        metadata={
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "chunk_index": chunk.chunk_index,
        },
        project_id=project_id,
        citation_label=_citation_label(knowledge_layer),
    )


def retrieve_foundation_evidence(
    query: str,
    top_k: int = 5,
) -> List[AutomotiveEvidence]:
    """Retrieve evidence from the automotive foundation corpus."""
    if not query or not query.strip():
        return []

    tokens: List[str] = []
    campaign = _extract_campaign_number(query)
    investigation = _extract_investigation_number(query)
    if campaign:
        tokens.append(campaign)
    if investigation:
        tokens.append(investigation)

    scored = _search_project(FOUNDATION_PROJECT_ID, query, top_k, tokens)
    return [
        _to_evidence(score, chunk, knowledge_layer=FOUNDATION_PROJECT_ID)
        for score, chunk in scored
    ]


def retrieve_evidence(request: RetrievalRequest) -> List[AutomotiveEvidence]:
    """Layer-aware retrieval across foundation and optional private project layers."""
    if not request.query or not request.query.strip():
        return []

    tokens: List[str] = []
    campaign = _extract_campaign_number(request.query)
    investigation = _extract_investigation_number(request.query)
    if campaign:
        tokens.append(campaign)
    if investigation:
        tokens.append(investigation)

    results: List[AutomotiveEvidence] = []
    layers = request.knowledge_layers or ["automotive_core_v1"]

    if "client_private" in layers and request.project_id:
        private_scored = _search_project(
            request.project_id, request.query, request.top_k, tokens
        )
        results.extend(
            _to_evidence(
                score,
                chunk,
                knowledge_layer=CLIENT_PRIVATE_LAYER,
                project_id=request.project_id,
            )
            for score, chunk in private_scored
        )

    if "automotive_core_v1" in layers:
        foundation_scored = _search_project(
            FOUNDATION_PROJECT_ID, request.query, request.top_k, tokens
        )
        results.extend(
            _to_evidence(score, chunk, knowledge_layer=FOUNDATION_PROJECT_ID)
            for score, chunk in foundation_scored
        )

    # Prefer private evidence for project-scoped questions while keeping
    # foundation supplements available.
    results.sort(
        key=lambda e: (
            0 if e.knowledge_layer == CLIENT_PRIVATE_LAYER else 1,
            -e.retrieval_score,
        )
    )
    return results[: request.top_k]


def retrieve_by_campaign_number(campaign_number: str) -> List[AutomotiveEvidence]:
    """Exact campaign-number lookup."""
    return retrieve_foundation_evidence(
        query=f"recall campaign {campaign_number}",
        top_k=5,
    )


def retrieve_by_investigation_number(
    investigation_number: str,
) -> List[AutomotiveEvidence]:
    """Exact investigation-number lookup."""
    return retrieve_foundation_evidence(
        query=f"investigation {investigation_number}",
        top_k=5,
    )


def evidence_to_citation_payload(evidence: AutomotiveEvidence) -> Dict[str, Any]:
    """Serialize evidence into the chat SSE citation envelope."""
    return {
        "knowledge_layer": evidence.knowledge_layer,
        "collection_id": evidence.knowledge_layer,
        "foundation_pack_id": evidence.foundation_pack_id,
        "project_id": evidence.project_id,
        "source_family": evidence.source_family,
        "source_title": evidence.source_title,
        "source_authority": evidence.source_authority,
        "source_url": evidence.source_url,
        "record_reference": evidence.record_reference,
        "retrieval_score": evidence.retrieval_score,
        "citation_label": evidence.citation_label,
        "chunk_text": evidence.chunk_text,
        "metadata": evidence.metadata,
    }
