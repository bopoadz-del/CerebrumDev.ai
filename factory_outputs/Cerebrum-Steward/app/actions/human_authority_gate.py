"""Generated action module for estate.human_authority_gate (strategy=GENERATE).

Uses cerebrum_product_kernel ActionOutcome patterns. Durable logic belongs in
Factory templates / dual-registered blocks — regenerate rather than hand-edit.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.cerebrum_product_kernel.contract.models import (
    ActionEvidence,
    ActionOutcome,
    ActionStatus,
)

ACTION_ID = "estate.human_authority_gate"
CAPABILITY_ID = "human_authority_gate"
STRATEGY = "GENERATE"
BLOCK_IDS: List[str] = []


async def handle(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute capability with kernel-shaped outcome (not a freehand echo)."""
    tenant_id = context.get("tenant_id")
    if not tenant_id:
        outcome = ActionOutcome(
            status=ActionStatus.PERMISSION_DENIED,
            error_code="missing_tenant",
            error_message="tenant_id is required in trusted context",
        )
        return outcome.to_dict()

    if STRATEGY == "UNSUPPORTED":
        outcome = ActionOutcome(
            status=ActionStatus.UNSUPPORTED,
            error_code="unsupported_capability",
            error_message=f"capability {CAPABILITY_ID} is UNSUPPORTED",
        )
        return outcome.to_dict()

    evidence = [
        ActionEvidence(
            source_id=bid,
            filename=f"block:{bid}",
            excerpt=f"Resolved via dual-registered block {bid}",
            metadata={"strategy": STRATEGY},
        )
        for bid in BLOCK_IDS
    ]
    output = {
        "action_id": ACTION_ID,
        "capability_id": CAPABILITY_ID,
        "strategy": STRATEGY,
        "block_ids": BLOCK_IDS,
        "arguments": arguments or {},
        "tenant_id": tenant_id,
        "result": {
            "ok": True,
            "summary": f"{CAPABILITY_ID} executed via Factory template ({STRATEGY})",
            "blocks_used": BLOCK_IDS,
        },
    }
    outcome = ActionOutcome.success(output, evidence=evidence)
    return outcome.to_dict()
