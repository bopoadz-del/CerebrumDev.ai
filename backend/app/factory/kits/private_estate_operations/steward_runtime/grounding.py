"""Grounding stage for the steward retrieval API — mandatory, audited.

Retrieval endpoints return source-backed hits, so the verdict model is:
- ``grounded``              at least one hit; every hit carries provenance
- ``insufficient_sources``  zero hits; the caller receives no fabricated
                            fallback — the answer surface is null

Every verdict is persisted to the append-only audit ledger via
``persist_audit_event`` with ``action_id="grounding.verdict"``.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.steward.audit_store import persist_audit_event

VERDICT_GROUNDED = "grounded"
VERDICT_INSUFFICIENT = "insufficient_sources"


def retrieval_verdict(hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verdict for a retrieval result set."""
    if not hits:
        return {
            "verdict": VERDICT_INSUFFICIENT,
            "answer": None,
            "hit_count": 0,
        }
    return {
        "verdict": VERDICT_GROUNDED,
        "hit_count": len(hits),
    }


def record_verdict(
    session: Session,
    *,
    tenant_id: str,
    estate_id: Optional[str],
    user_id: str,
    query: str,
    verdict: Dict[str, Any],
    surface: str,
) -> Dict[str, Any]:
    """Persist one grounding verdict to the audit ledger."""
    return persist_audit_event(
        session,
        tenant_id=tenant_id,
        estate_id=estate_id,
        action_id="grounding.verdict",
        request_id=str(uuid.uuid4()),
        status=verdict["verdict"],
        user_id=user_id,
        detail=f"{surface}: {query[:200]}",
        metadata={"surface": surface, **verdict},
    )
