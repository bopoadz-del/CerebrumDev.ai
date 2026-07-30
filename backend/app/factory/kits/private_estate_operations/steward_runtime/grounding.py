"""Grounding stage for the steward retrieval API — mandatory, audited.

Retrieval endpoints return source-backed hits, so the verdict model is:
- ``grounded``              at least one hit; every hit carries provenance
- ``insufficient_sources``  zero hits; the caller receives no fabricated
                            fallback — the answer surface is null

Every verdict is persisted to the append-only audit ledger via
``persist_audit_event`` with ``action_id="grounding.verdict"``.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.steward.audit_store import persist_audit_event

VERDICT_GROUNDED = "grounded"
VERDICT_INSUFFICIENT = "insufficient_sources"
VERDICT_OUT_OF_SCOPE = "out_of_scope"

# Scope refusal — questions this product never attempts, even when the
# corpus could ground an answer. Extend per product via
# STEWARD_SCOPE_REFUSALS_PATH ([{"id","pattern","reason"}, ...]).
_DEFAULT_SCOPE_REFUSALS = [
    {
        "id": "medication_dosing",
        "pattern": r"\b(dose|dosage|dosing|administer)\b.*\b(patient|medication|drug|mg|ml)\b"
        r"|\b(patient|medication|drug)\b.*\b(dose|dosage|dosing|administer)\b",
        "reason": "Medication dosing is a clinical decision; this system never answers it.",
    },
    {
        "id": "structural_signoff",
        "pattern": r"\b(certify|sign[- ]?off|approve)\b.*\b(structural|beam|column|foundation|load[- ]?bearing)\b",
        "reason": "Structural adequacy certification requires a licensed engineer; this system never signs off.",
    },
    {
        "id": "legal_filing",
        "pattern": r"\b(file|filing|statute of limitations|court deadline)\b.*\b(lawsuit|claim|court)\b",
        "reason": "Legal filing strategy and deadlines require counsel; this system never advises on them.",
    },
    {
        "id": "life_safety_emergency",
        "pattern": r"\b(emergency|evacuat\w+|mayday|engine (failure|fire))\b.*\b(now|immediately|right now)\b",
        "reason": "Live emergency response belongs to certified operators and official procedures, not this system.",
    },
]

_SCOPE_CACHE: Optional[list] = None


def _scope_refusals() -> list:
    global _SCOPE_CACHE
    if _SCOPE_CACHE is not None:
        return _SCOPE_CACHE
    rules = list(_DEFAULT_SCOPE_REFUSALS)
    override = os.getenv("STEWARD_SCOPE_REFUSALS_PATH", "").strip()
    if override:
        try:
            rules = json.loads(Path(override).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # fail closed to defaults, never to an empty list
    _SCOPE_CACHE = [
        {**r, "_compiled": re.compile(r["pattern"], re.IGNORECASE | re.DOTALL)}
        for r in rules
        if r.get("pattern")
    ]
    return _SCOPE_CACHE


def check_scope_refusal(query: str) -> Optional[Dict[str, Any]]:
    """Return the matched refusal rule for ``query``, or None."""
    text = query or ""
    for rule in _scope_refusals():
        if rule["_compiled"].search(text):
            return {"id": rule["id"], "reason": rule["reason"]}
    return None


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
