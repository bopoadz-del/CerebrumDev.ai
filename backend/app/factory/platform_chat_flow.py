"""Chat-driven platform creation flow.

Bridges free-text chat messages onto the EXISTING session product state
machine (routers/session_product.py). No parallel machinery: the same
draft_blueprint_from_brief / plan_blueprint / generate_product functions,
the same ProductDesignState on the session. The chat is simply a second
front door onto the same house.

Flow:
  1. User message matches platform intent  -> draft_from_chat()
  2. User approves ("approve", "go ahead") -> approve_and_generate()
  3. Anything else -> falls through to the normal kit-configurator chat.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .blueprint import ProductBlueprint
from .paths import factory_repo_root
from .product_architect import (
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
)

# --- Intent detection -------------------------------------------------------

_PLATFORM_INTENT_RE = re.compile(
    r"\b(build|create|make|generate|assemble|design|spin\s*up|ship)\b"
    r"[\w\s,.'\-/:]{0,100}?"
    r"\b(platform|product|system|portal)\b",
    re.IGNORECASE,
)

# Kit-configurator vocabulary: messages about chains/blocks/kits/domains stay
# in the legacy chat flow even if they also mention a platform noun.
_KIT_CONFIG_RE = re.compile(
    r"\b(chain|block|blocks|kit|kits|domain|lora|learning\s*rate|vector\s*db|hnsw)\b",
    re.IGNORECASE,
)

_APPROVAL_RE = re.compile(
    r"^\s*(approve|approved|go\s*ahead|looks\s*good|generate\s*it|"
    r"build\s*it|yes\b.*\b(build|generate|approve))",
    re.IGNORECASE,
)


def is_platform_intent(message: str) -> bool:
    """True when the message asks the factory to create a platform/product."""
    text = message or ""
    if _KIT_CONFIG_RE.search(text):
        return False
    return bool(_PLATFORM_INTENT_RE.search(text))


def is_approval(message: str) -> bool:
    """True when the message is a short approval of a pending blueprint."""
    return bool(_APPROVAL_RE.search(message or ""))


# --- State helpers ----------------------------------------------------------

def has_pending_blueprint(state: Any) -> bool:
    pd = getattr(state, "product_design", None)
    return bool(pd and pd.blueprint and not pd.blueprint_approved)


def _blocks_root() -> Optional[Path]:
    root = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    return Path(root) if root else None


def _session_output(session_id: str, product_id: str, output_root: Optional[Path]) -> Path:
    if output_root is not None:
        return Path(output_root) / product_id
    return factory_repo_root() / "factory_outputs" / "sessions" / session_id / product_id


# --- Flow actions ------------------------------------------------------------

def draft_from_chat(state: Any, message: str) -> Dict[str, Any]:
    """Draft a blueprint from a chat brief and park it on the session.

    Mutates state.product_design exactly like POST /product/draft.
    Returns the summary payload streamed back to the chat.
    """
    bp = draft_blueprint_from_brief(message, vertical_hint=None)
    pd = state.product_design
    pd.brief = message
    pd.blueprint = bp.model_dump(mode="json")
    pd.plan = None
    pd.blueprint_approved = False
    pd.generation = None
    pd.last_error = None
    pd.mode = "product"

    capabilities = [c.id for c in bp.capabilities]
    blocks = sorted({b for c in bp.capabilities for b in c.block_ids})
    source = "golden_steward" if bp.product_id == "cerebrum-steward" else "drafted"
    summary = (
        f"Blueprint drafted: {bp.product_name} ({bp.vertical}). "
        f"{len(capabilities)} capabilities, {len(blocks)} blocks. "
        f"Reply 'approve' to generate the product, or refine your brief."
    )
    return {
        "ok": True,
        "source": source,
        "blueprint": pd.blueprint,
        "yaml": blueprint_to_yaml(bp),
        "summary": summary,
    }


def approve_and_generate(
    state: Any,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Approve the pending blueprint, plan it, and generate the product.

    Mutates state.product_design exactly like POST /product/approve +
    /product/generate. Returns the generation payload.
    """
    pd = state.product_design
    if not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")

    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()

    out = _session_output(state.session_id, bp.product_id, output_root)
    result = generate_product(bp, out, blocks_root=_blocks_root())
    pd.generation = {
        "output_dir": result["output_dir"],
        "inputs_hash": result["inputs_hash"],
        "product_id": result["product_id"],
    }
    pd.last_error = None

    summary = (
        f"Product generated: {result['product_id']} -> {result['output_dir']}. "
        f"Open the Deploy panel (Phase 5) to package and ship it."
    )
    return {
        "ok": True,
        "generation": pd.generation,
        "plan": pd.plan,
        "summary": summary,
    }
