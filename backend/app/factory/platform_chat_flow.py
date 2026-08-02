"""Chat-driven platform creation flow.

Bridges free-text chat messages onto the EXISTING session product state
machine (routers/session_product.py). No parallel machinery: the same
draft_blueprint_from_brief / plan_blueprint / generate_product functions,
the same ProductDesignState on the session. The chat is simply a second
front door onto the same house.

Routing contract (this is law, the smoke tests enforce it):
  1. Explicit commands ALWAYS enter the platform flow:
     "/platform <brief>", "new platform <brief>", "platform: <brief>".
  2. Free-text NLP intent ("build me a platform for hotels") enters the
     platform flow by DEFAULT — factory doctrine: the chat's purpose is
     building platforms. Set PLATFORM_CHAT_FLOW_ENABLED=off to keep the
     legacy kit-configurator routing for unauthenticated deployments.
  3. Approval ("approve", "go ahead") is only intercepted when a blueprint
     is actually pending on the session.
  4. Kit-configurator vocabulary (chain/blocks/kits/domain/lora/...) stays
     in the legacy chat flow even when it also mentions a platform noun.
  5. Anything else falls through to the normal kit-configurator chat.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from .blueprint import CapabilitySpec, ProductBlueprint
from .dual_registry import dual_registered_ids
from .paths import factory_repo_root
from .product_architect import (
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
)

# --- Intent detection -------------------------------------------------------

_PLATFORM_INTENT_RE = re.compile(
    r"\b(build|create|make|generate|assemble|design|spin\s*up|ship|"
    r"want|need|give\s*me|get\s*me|set\s*up|looking\s+for|i'?d\s+like)\b"
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

# Explicit commands: deterministic entry into the platform flow, always on.
_EXPLICIT_CMD_RE = re.compile(
    r"^\s*(?:/platform\b|new\s+platform\b|platform\s*:)",
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


def is_explicit_platform_command(message: str) -> bool:
    """True for explicit commands (/platform, 'new platform', 'platform:')."""
    return bool(_EXPLICIT_CMD_RE.search(message or ""))


def platform_chat_enabled() -> bool:
    """Free-text NLP interception gate.

    Default ON (factory doctrine: the chat's purpose is building platforms).
    Set PLATFORM_CHAT_FLOW_ENABLED to 0/false/no/off to force the legacy
    kit-configurator routing.
    """
    return os.getenv("PLATFORM_CHAT_FLOW_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def should_handle_platform_message(message: str) -> bool:
    """Router contract: should this chat message enter the platform flow?

    Explicit commands always qualify. Free-text intent qualifies when the
    platform chat gate is on (default). Kit-configurator vocabulary stays in
    the legacy chat. Everything else falls through to the legacy chat.
    """
    text = message or ""
    if is_explicit_platform_command(text):
        return True
    if not platform_chat_enabled():
        return False
    return is_platform_intent(text)


def is_approval(message: str) -> bool:
    """True when the message is a short approval of a pending blueprint."""
    return bool(_APPROVAL_RE.search(message or ""))


# --- State helpers ----------------------------------------------------------

def has_pending_blueprint(state: Any) -> bool:
    pd = getattr(state, "product_design", None)
    return bool(pd and pd.blueprint and not pd.blueprint_approved)


def _blocks_root() -> Optional[Path]:
    """Resolve a real Cerebrum-Blocks checkout for the generator to vendor
    REAL block code from.

    Order: (1) explicit local path via CEREBRUM_BLOCKS_ROOT/_PATH; (2) clone
    the Store repo (CEREBRUM_BLOCKS_REPO, GITHUB_TOKEN-auth'd) via
    engine_discovery. Returns None only when both fail — in which case the
    generator falls back to its vendor-mirror stubs (honestly labeled).

    This is the fix for hollow products: previously only the env path was
    consulted, so on any deploy without a local checkout every referenced
    block was echo-stubbed. Now the factory clones the Store and vendors the
    canonical block code (block_registry/<id>) the plan references.
    """
    root = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    if root:
        return Path(root)
    try:
        from app.core import engine_discovery
        checkout = engine_discovery.find_engine_root()
        # Only use it if it actually carries the block registry the generator
        # copies from — otherwise it's not a usable blocks root.
        if checkout and (Path(checkout) / "block_registry").is_dir():
            logger.info("blocks_root: cloned Store checkout at %s", checkout)
            return Path(checkout)
        logger.warning("blocks_root: checkout %s has no block_registry/", checkout)
    except Exception as exc:  # noqa: BLE001 — never break generation on clone failure
        logger.warning("blocks_root: Store clone unavailable (%s); vendor mirror will be used", exc)
    return None


def _session_output(session_id: str, product_id: str, output_root: Optional[Path]) -> Path:
    if output_root is not None:
        return Path(output_root) / product_id
    return factory_repo_root() / "factory_outputs" / "sessions" / session_id / product_id


# --- Refinement commands ------------------------------------------------------

_ADD_CAP_RE = re.compile(
    r"(?:add|include)\s+(?:capability\s+)?([a-z0-9_]+)", re.IGNORECASE
)
_REMOVE_CAP_RE = re.compile(
    r"(?:remove|drop|exclude)\s+(?:capability\s+)?([a-z0-9_]+)", re.IGNORECASE
)
_RENAME_RE = re.compile(
    r"(?:rename\s+(?:product\s+)?to\s+|product\s+name\s+(?:is\s+)?)\"?([^\"\n]+)\"?",
    re.IGNORECASE,
)
_VERTICAL_RE = re.compile(
    r"(?:change\s+vertical\s+to\s+|vertical\s+(?:is\s+)?)\"?([a-z0-9_]+)\"?",
    re.IGNORECASE,
)
_LIST_CAPS_RE = re.compile(
    r"^(?:list\s+capabilities|show\s+capabilities|what\s+is\s+in\s+the\s+blueprint|show\s+blueprint)$",
    re.IGNORECASE,
)


def _capability_for_id(cap_id: str, dual_ids: List[str]) -> Dict[str, Any]:
    """Build a minimal capability spec for a user-added capability id."""
    cap_id = re.sub(r"[^a-z0-9_]+", "_", cap_id.lower()).strip("_")
    human = cap_id.replace("_", " ").title()
    if cap_id in dual_ids:
        return {
            "id": cap_id,
            "description": f"{human} capability (reused from the block store)",
            "block_ids": [cap_id],
            "strategy_hint": "REUSE",
            "required": True,
        }
    return {
        "id": cap_id,
        "description": f"{human} capability — generated scaffolding, extend via Factory templates",
        "block_ids": [],
        "strategy_hint": "GENERATE",
        "required": True,
    }


def parse_refinement_command(message: str) -> tuple[str, Dict[str, Any]]:
    """Parse a blueprint refinement command. Returns ('', {}) if not a command."""
    text = (message or "").strip()
    m = _ADD_CAP_RE.search(text)
    if m:
        return "add_capability", {"cap_id": m.group(1)}
    m = _REMOVE_CAP_RE.search(text)
    if m:
        return "remove_capability", {"cap_id": m.group(1)}
    m = _RENAME_RE.search(text)
    if m:
        return "rename_product", {"name": m.group(1).strip()}
    m = _VERTICAL_RE.search(text)
    if m:
        return "set_vertical", {"vertical": m.group(1).strip()}
    if _LIST_CAPS_RE.search(text):
        return "list_capabilities", {}
    return "", {}


def refine_from_chat(state: Any, message: str) -> Optional[Dict[str, Any]]:
    """Apply a refinement command to the pending blueprint and return a summary.

    Returns None if the message is not a refinement command.
    """
    pd = getattr(state, "product_design", None)
    if not pd or not pd.blueprint:
        return None

    action, args = parse_refinement_command(message)
    if not action:
        return None

    bp = ProductBlueprint.model_validate(pd.blueprint)
    caps = [c.model_dump(mode="json") for c in bp.capabilities]
    cap_ids = {c["id"] for c in caps}

    if action == "add_capability":
        cap_id = args["cap_id"]
        if cap_id in cap_ids:
            return {
                "ok": True,
                "refined": True,
                "action": action,
                "summary": f"Capability '{cap_id}' is already in the blueprint.",
                "blueprint": pd.blueprint,
                "yaml": blueprint_to_yaml(bp),
            }
        dual = sorted(dual_registered_ids())
        caps.append(_capability_for_id(cap_id, dual))

    elif action == "remove_capability":
        cap_id = args["cap_id"]
        new_caps = [c for c in caps if c["id"] != cap_id]
        if len(new_caps) == len(caps):
            return {
                "ok": True,
                "refined": True,
                "action": action,
                "summary": f"Capability '{cap_id}' was not found in the blueprint.",
                "blueprint": pd.blueprint,
                "yaml": blueprint_to_yaml(bp),
            }
        caps = new_caps
        if not caps:
            return {
                "ok": False,
                "refined": False,
                "action": action,
                "summary": "Cannot remove the last capability — a blueprint needs at least one.",
                "blueprint": pd.blueprint,
            }

    elif action == "rename_product":
        bp.product_name = args["name"][:120]
        pd.blueprint = bp.model_dump(mode="json")
        return {
            "ok": True,
            "refined": True,
            "action": action,
            "summary": f"Product renamed to '{bp.product_name}'.",
            "blueprint": pd.blueprint,
            "yaml": blueprint_to_yaml(bp),
        }

    elif action == "set_vertical":
        vertical = re.sub(r"[^a-z0-9_]", "_", args["vertical"].lower()).strip("_")[:48]
        bp.vertical = vertical
        bp.product_id = vertical
        pd.blueprint = bp.model_dump(mode="json")
        return {
            "ok": True,
            "refined": True,
            "action": action,
            "summary": f"Vertical set to '{vertical}'.",
            "blueprint": pd.blueprint,
            "yaml": blueprint_to_yaml(bp),
        }

    elif action == "list_capabilities":
        lines = [f"- {c['id']} ({c.get('strategy_hint','?')})" for c in caps]
        return {
            "ok": True,
            "refined": False,
            "action": action,
            "summary": f"Blueprint '{bp.product_name}' has {len(caps)} capabilities:\n" + "\n".join(lines),
            "blueprint": pd.blueprint,
            "yaml": blueprint_to_yaml(bp),
        }

    # Rebuild blueprint after add/remove
    bp.capabilities = [CapabilitySpec.model_validate(c) for c in caps]
    pd.blueprint = bp.model_dump(mode="json")
    pd.plan = None  # force re-plan after change
    pd.generation = None
    yaml_text = blueprint_to_yaml(bp)
    return {
        "ok": True,
        "refined": True,
        "action": action,
        "summary": (
            f"Blueprint updated: {len(bp.capabilities)} capabilities. "
            "Reply 'approve' to build, or keep refining."
        ),
        "blueprint": pd.blueprint,
        "yaml": yaml_text,
    }


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
    # Say who drafted it. A dead LLM key must not look identical to a
    # working architect.
    mode_labels = {
        "architect_llm": "Drafted by the architect LLM.",
        "golden_steward": "Drafted from the golden steward blueprint.",
        "keyword_fallback": "Drafted by deterministic templates (no LLM).",
    }
    mode_line = mode_labels.get(bp.drafting_mode or "", "")
    if bp.drafting_note:
        mode_line = (mode_line + " " + bp.drafting_note + ".").strip()
    summary = (
        f"Blueprint drafted: {bp.product_name} ({bp.vertical}). "
        f"{len(capabilities)} capabilities, {len(blocks)} blocks. "
        + (mode_line + " " if mode_line else "")
        + "Reply 'approve' to generate the product, or refine your brief."
    )
    return {
        "ok": True,
        "source": source,
        "drafting_mode": bp.drafting_mode,
        "drafting_note": bp.drafting_note,
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
        "coder": result.get("coder"),
    }
    pd.last_error = None

    # Say what generation actually is: deterministic composition of prebuilt
    # blocks (plus LLM-written handlers for GENERATE capabilities when the
    # coder runs). "Generated" alone oversold this as bespoke code.
    strategies = [c.get("strategy") for c in (pd.plan or {}).get("capabilities", [])]
    reuse_n = sum(1 for s in strategies if s == "REUSE")
    composition = f"composed from {reuse_n} prebuilt block capabilities"
    coder = result.get("coder") or {}
    if coder.get("written"):
        composition += f" + {len(coder['written'])} coder-written"
    summary = (
        f"Product generated: {result['product_id']} ({composition}). "
        f"Download it from Your Platforms (product package) or keep refining."
    )
    stubbed = coder.get("stubbed") or {}
    if stubbed:
        # Degraded output is fine; invisible degradation is not.
        names = ", ".join(sorted(stubbed))
        reason = next(iter(stubbed.values()))
        summary += (
            f" Note: {len(stubbed)} capability(ies) shipped as honest stubs "
            f"— the coder could not write them ({names}: {reason})."
        )
    return {
        "ok": True,
        "generation": pd.generation,
        "plan": pd.plan,
        "summary": summary,
    }
