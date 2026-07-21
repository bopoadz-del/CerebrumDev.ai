"""Resident Mode autonomy levels — L3–L5 are draft-only."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from app.resident_engineer.injection_guard import strip_instruction_patterns

AutonomyLevel = Literal["L1", "L2", "L3", "L4", "L5"]

DRAFT_ONLY_LEVELS = frozenset({"L3", "L4", "L5"})


def draft_change_request(
    *,
    level: AutonomyLevel,
    kind: str,
    summary: str,
    product_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Surface L3–L5 work as a draft change request — never execute."""
    if level not in DRAFT_ONLY_LEVELS and level not in {"L1", "L2"}:
        raise ValueError(f"unknown autonomy level: {level}")
    executable = level in {"L1", "L2"}
    return {
        "schema_version": "1.0.0",
        "kind": kind,
        "level": level,
        "product_id": product_id,
        "summary": strip_instruction_patterns(summary or ""),
        "details": details or {},
        "execution": "allowed" if executable and level == "L2" else "draft_only",
        "status": "draft" if level in DRAFT_ONLY_LEVELS else "observable",
        "note": (
            "L3–L5 produce draft change requests only; intake/execution is Milestone 3+"
            if level in DRAFT_ONLY_LEVELS
            else "Resident Mode L1/L2 surface"
        ),
    }
