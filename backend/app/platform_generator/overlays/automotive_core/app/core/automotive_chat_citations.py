"""Chat citation helpers for the automotive grounded assistant.

The generated platform's chat router should:

1. Call ``gather_automotive_context`` before LLM generation.
2. Inject evidence text into the system/user context.
3. Emit an SSE ``citations`` event via ``citations_sse_event``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.core.automotive_retrieval import (
    RetrievalRequest,
    evidence_to_citation_payload,
    retrieve_evidence,
)


def gather_automotive_context(
    query: str,
    *,
    project_id: Optional[str] = None,
    include_private: bool = True,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Retrieve evidence and build prompt context + citation payload."""
    layers: List[str] = ["automotive_core_v1"]
    if include_private and project_id:
        layers = ["client_private", "automotive_core_v1"]

    evidence = retrieve_evidence(
        RetrievalRequest(
            query=query,
            knowledge_layers=layers,  # type: ignore[arg-type]
            project_id=project_id,
            top_k=top_k,
        )
    )
    citations = [evidence_to_citation_payload(item) for item in evidence]
    context_blocks = []
    for item in evidence:
        label = item.citation_label
        context_blocks.append(
            f"[{label}] {item.source_title} ({item.record_reference})\n{item.chunk_text}"
        )
    return {
        "evidence": evidence,
        "citations": citations,
        "context_text": "\n\n".join(context_blocks),
    }


def citations_sse_event(citations: List[Dict[str, Any]]) -> str:
    """Format citations as a Server-Sent Event payload string."""
    payload = {"type": "citations", "citations": citations}
    return f"data: {json.dumps(payload)}\n\n"


def format_citation_labels(citations: List[Dict[str, Any]]) -> List[str]:
    """Human-readable citation lines for UI panels."""
    lines = []
    for c in citations:
        label = c.get("citation_label") or "Automotive Core"
        title = c.get("source_title") or c.get("record_reference") or "Evidence"
        lines.append(f"[{label}] {title}")
    return lines
