"""Factory Floor chat LLM — the door that starts the coding agent.

The Floor used to intercept "approve" with a regex and never ask the chat
model anything. That meant the coding agent was a keyword side-door, not
something the conversation started.

When a factory LLM key is configured, platform chat messages go through
this orchestrator. The model returns a JSON action:

  draft_platform  — park a blueprint (architect LLM still drafts it)
  start_coder     — approve the pending blueprint and launch WRITER
  refine_blueprint — apply a refinement command already understood by
                     platform_chat_flow.refine_from_chat
  reply           — talk, do not start the coder

Kit-configurator vocabulary never enters this path. Regex approval remains
the offline fallback when the LLM is unset or the call fails.

The coding agent still lives only inside WRITER (see
docs/factory/AGENT_IN_THE_KERNELS.md). This module does not move it into
COLLECTOR or TESTER; it only decides *when* WRITER starts.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.core.llm_config import get_factory_llm_config
from app.factory import platform_chat_flow
from app.factory.product_architect import _llm_json_call

logger = logging.getLogger(__name__)

CHAT_LLM_ENV = "PLATFORM_CHAT_LLM_ENABLED"

_ACTIONS = frozenset({"draft_platform", "start_coder", "refine_blueprint", "reply"})

_SYSTEM = """You are the Cerebrum Factory Floor chat. Users describe software \
platforms in this conversation. You do not configure kit chains or invent \
block ids. You pick exactly one JSON action.

Actions:
- draft_platform: the user wants a new platform / product. Set "brief" to the \
user's description (or a cleaned restatement). This drafts a blueprint; it \
does NOT start the coding agent yet. Never draft_platform on continue/resume \
— that would wipe an in-flight run.
- start_coder: launch or resume the coding agent (WRITER). Call it when \
(1) a blueprint is pending AND the user confirms (approve, go ahead, looks \
good, build it, ship it, yes), OR (2) a coding run is in-flight, stalled, \
or interrupted and the user says continue / resume / keep going. Do not \
require a pending unapproved blueprint to resume. If the last run already \
succeeded AND the product is pilot-ready, do NOT call start_coder — reply \
that it finished. If code-phase 5/5 succeeded but it is NOT pilot-ready, \
continue/resume MUST call start_coder to open a pilot cycle. Do not call a \
code-cycle SUCCESS "finished" or "download ready".
- refine_blueprint: the user wants to change the pending blueprint. Set \
"refine_message" to a command the factory already understands, e.g. \
"add capability inventory", "remove capability audit", \
"rename product to Harbor Ops", "list capabilities".
- reply: questions, chit-chat, or a pending blueprint with no confirmation. \
Set "message" to a short grounded reply. Tell them they can confirm to start \
the coding agent. Do not pretend a build started.

Return ONLY JSON: {"action": "...", "brief": "", "refine_message": "", "message": ""}.
"""


def chat_llm_enabled() -> bool:
    """Route Floor chat through the LLM when factory credentials exist.

    Explicit ``PLATFORM_CHAT_LLM_ENABLED=0`` keeps regex-only routing.
    Explicit ``1`` forces the LLM path (call failures still fall back).
    Unset means: orchestrate whenever a factory API key is configured.
    """
    raw = os.getenv(CHAT_LLM_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    cfg = get_factory_llm_config()
    if cfg.get("error") or cfg.get("mock"):
        return False
    return bool(cfg.get("api_key"))


def should_orchestrate(state: Any, message: str) -> bool:
    """True when this message should be decided by the Floor chat LLM."""
    if not chat_llm_enabled():
        return False
    if not (message or "").strip():
        return False
    if platform_chat_flow.is_kit_config_vocabulary(message):
        return False
    # The Floor is a product factory. When the LLM is keyed, let it classify
    # business briefs that never say "platform" — regex intent is the offline
    # fallback, not the live door.
    return True


def _session_facts(state: Any) -> str:
    pd = getattr(state, "product_design", None)
    if not pd or not getattr(pd, "blueprint", None):
        return "Session: no pending blueprint. start_coder is forbidden."
    bp = pd.blueprint or {}
    caps = bp.get("capabilities") or []
    cap_ids = ", ".join(str(c.get("id") or "") for c in caps if isinstance(c, dict))
    pending = not bool(pd.blueprint_approved)
    lines = [
        f"Pending blueprint: {'yes' if pending else 'no'}.",
        f"Product: {bp.get('product_name')} (vertical={bp.get('vertical')}).",
        f"Capabilities: {cap_ids or '(none)'}.",
    ]
    if getattr(pd, "generation", None):
        gen = pd.generation or {}
        lines.append(f"Last generation: {gen.get('product_id')}.")
        if gen.get("inputs_hash"):
            lines.append(f"Blueprint hash: {str(gen.get('inputs_hash'))[:12]}.")
        if gen.get("output_dir"):
            lines.append(f"Output dir: {gen.get('output_dir')}.")
        if gen.get("phases_done") is not None:
            lines.append(f"Phases done: {gen.get('phases_done')}.")
    if platform_chat_flow.is_pilot_ready(state):
        lines.append(
            "Product is pilot-ready (Store-green). start_coder is forbidden — "
            "tell the user it already finished; do not start a new product."
        )
    elif platform_chat_flow.is_generation_complete(state):
        lines.append(
            "Code-phase 5/5 SUCCEEDED but the platform is NOT pilot-ready "
            "(pytest -m pilot / Store ops still open). continue/resume MUST "
            "call start_coder — that opens a pilot cycle on the same hash. "
            "Do not draft a new platform."
        )
    elif platform_chat_flow.is_generation_resumable(state):
        point = platform_chat_flow._ledger_resume_point(state) or "the last phase"
        lines.append(
            f"Coding run is incomplete (resume at {point}). "
            "continue/resume MUST call start_coder — that resumes the same "
            "hash. Do not draft a new platform. A pending blueprint is not required."
        )
    elif pending:
        lines.append("The user must confirm before you call start_coder.")
    else:
        lines.append(
            "No pending blueprint and no interrupted coding run. "
            "start_coder is forbidden."
        )
    return " ".join(lines)


def decide(state: Any, message: str) -> Dict[str, Any]:
    """Ask the factory LLM which Floor action to take. Raises on failure."""
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": _session_facts(state) + "\n\nUser message:\n" + (message or ""),
        },
    ]
    data = _llm_json_call(messages)
    if not isinstance(data, dict):
        raise ValueError("Floor chat LLM returned a non-object")
    action = str(data.get("action") or "").strip()
    if action not in _ACTIONS:
        raise ValueError(f"Floor chat LLM returned unknown action {action!r}")
    return {
        "action": action,
        "brief": str(data.get("brief") or "").strip(),
        "refine_message": str(data.get("refine_message") or "").strip(),
        "message": str(data.get("message") or "").strip(),
    }


def coerce_explicit_approval(decision: Dict[str, Any], state: Any, message: str) -> Dict[str, Any]:
    """If the model forgets the tool on an explicit 'approve', still start WRITER.

    The Approve button sends the word 'approve'. A rambling completion must
    not strand a confirmed blueprint. Natural-language confirms rely on the
    model; this seatbelt is only for is_approval() messages.
    """
    if decision.get("action") == "start_coder":
        return decision
    if platform_chat_flow.has_pending_blueprint(state) and platform_chat_flow.is_approval(message):
        return {
            "action": "start_coder",
            "brief": "",
            "refine_message": "",
            "message": "",
            "coerced": True,
        }
    if (
        platform_chat_flow.is_resume_request(message)
        or platform_chat_flow.is_pilot_request(message)
    ) and (
        platform_chat_flow.has_pending_blueprint(state)
        or platform_chat_flow.is_generation_resumable(state)
        or platform_chat_flow.is_generation_complete(state)
    ):
        return {
            "action": "start_coder",
            "brief": "",
            "refine_message": "",
            "message": "",
            "coerced": True,
        }
    return decision


def apply_decision(state: Any, message: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a decided Floor action against the session product state."""
    action = decision.get("action")

    if action == "start_coder":
        result = platform_chat_flow.start_or_resume_coder(
            state, triggered_by="chat_llm"
        )
        if result.get("generation") and not result.get("already_complete"):
            result["sse"] = result.get("sse") or "generation"
            result["stream_delta"] = False
        return result

    if action == "draft_platform":
        brief = decision.get("brief") or message
        result = platform_chat_flow.draft_from_chat(state, brief)
        result["sse"] = "blueprint"
        result["stream_delta"] = False
        return result

    if action == "refine_blueprint":
        refine_msg = decision.get("refine_message") or message
        refined = platform_chat_flow.refine_from_chat(state, refine_msg)
        if refined:
            refined["sse"] = "blueprint" if refined.get("refined") else "info"
            refined["stream_delta"] = not refined.get("refined")
            return refined
        return {
            "sse": "info",
            "ok": True,
            "summary": (
                "I could not apply that refinement. Try 'add capability X', "
                "'remove capability X', or confirm to start the coding agent."
            ),
            "stream_delta": True,
        }

    summary = decision.get("message") or (
        "A blueprint is drafted. Confirm to start the coding agent, or refine it."
        if platform_chat_flow.has_pending_blueprint(state)
        else "Describe the platform you want and I will draft a blueprint."
    )
    return {"sse": "info", "ok": True, "summary": summary, "stream_delta": True}


def try_handle(state: Any, message: str) -> Optional[Dict[str, Any]]:
    """Decide + apply. None means the caller should use the regex fallback."""
    if not should_orchestrate(state, message):
        return None
    try:
        decision = decide(state, message)
    except Exception:
        logger.exception("Floor chat LLM failed; falling back to regex routing")
        return None
    decision = coerce_explicit_approval(decision, state, message)
    return apply_decision(state, message, decision)
