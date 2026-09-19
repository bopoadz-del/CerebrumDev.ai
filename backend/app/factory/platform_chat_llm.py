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
  ask_user        — ask what the brief leaves open BEFORE drafting (who uses
                    it, documents to answer from, size, automation). The
                    model sees the conversation and the Store inventory;
                    the number of rounds is capped in code.

Kit-configurator vocabulary never enters this path. Exact ``approve`` /
``approved`` (the Approve button) skips the LLM and uses the regex door.
Other approval phrasing still asks the model; regex remains the fallback
when the LLM is unset, returns a soft miss (empty / ``{}``), or fails.

The coding agent still lives only inside WRITER (see
docs/factory/AGENT_IN_THE_KERNELS.md). This module does not move it into
COLLECTOR or TESTER; it only decides *when* WRITER starts.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.core.llm_config import get_llm_config
from app.factory import platform_chat_flow
from app.factory.product_architect import LlmSoftMiss
from app.factory.product_architect import _llm_json_call as _architect_llm_json_call

logger = logging.getLogger(__name__)

CHAT_LLM_ENV = "PLATFORM_CHAT_LLM_ENABLED"

_ACTIONS = frozenset(
    {"draft_platform", "start_coder", "refine_blueprint", "reply", "ask_user"}
)

#: How many times the Floor may ask before it must draft. Enforced in
#: ``enforce_elicitation_cap`` -- the model is told the number but is not
#: trusted with it: a chat that can ask forever never builds.
MAX_ELICITATION_ROUNDS = 2

#: Conversation the model is shown. The router used to send the current
#: message alone, so the chat could not hold a dialogue even in principle.
_HISTORY_TURNS = 12
_HISTORY_TURN_CHARS = 600

_SYSTEM = """You are the Cerebrum Factory Floor chat. Users describe software \
platforms in this conversation. You do not configure kit chains or invent \
block ids. You pick exactly one JSON action.

Actions:
- ask_user: the user described a platform but the brief leaves open things \
that change what gets built. Ask BEFORE drafting. Put at most 3 short \
questions in "message", in plain language, specific to THEIR business — \
never a generic form, never a question the conversation already answers. \
Worth knowing when unsaid: who will use it and roughly how many people / \
which roles; whether they have documents, manuals, price lists or procedures \
the platform should answer from (they can upload them); the size of the \
operation (sites, rooms, vehicles, staff); anything that should happen \
automatically, or a specialist assistant they want; their own formulas or \
rules of thumb. If the brief is already specific, or the user says just \
build it / skip / you decide, do NOT ask — call draft_platform. Never \
ask_user when a blueprint is pending or a coding run exists. The session \
facts say how many rounds of questions remain; at zero you must draft.
- draft_platform: the user wants a new platform / product. Set "brief" to a \
complete restatement of what they want, folding in EVERY answer they gave \
in the conversation (users, documents, size, automation, their own rules). \
This drafts a blueprint; it does NOT start the coding agent yet. Never draft_platform on continue/resume \
— that would wipe an in-flight run.
- start_coder: launch or resume the coding agent (WRITER). Call it when \
(1) a blueprint is pending AND the user confirms (approve, go ahead, looks \
good, build it, ship it, yes), OR (2) a coding run is in-flight, stalled, \
or interrupted (no RUN_FAILED) and the user says continue / resume / keep \
going, OR (3) the last run FAILED (rework exhausted / TESTER still red) \
and the user asks to continue or try again — that starts a FRESH workspace, \
not a resume of the dead ledger. Do not require a pending unapproved \
blueprint to resume. If the last run already succeeded AND the product is \
pilot-ready, do NOT call start_coder — reply that it finished. If \
code-phase 5/5 succeeded but it is NOT pilot-ready, continue/resume MUST \
call start_coder to open a pilot cycle. Do not call a code-cycle SUCCESS \
"finished" or "download ready". After a FAILED run a new brief MUST call \
draft_platform (new product); do not treat that brief as a resume.
- refine_blueprint: the user wants to change the pending blueprint. Set \
"refine_message" to a command the factory already understands, e.g. \
"add capability inventory", "remove capability audit", \
"rename product to Harbor Ops", "list capabilities".
- reply: questions, chit-chat, or a pending blueprint with no confirmation. \
Set "message" to a short grounded reply. Tell them they can confirm to start \
the coding agent. Do not pretend a build started.

The session facts carry the STORE: ready-made blocks, CONNECTORS and MCP \
parts that are attachable, parts that are in the store but NOT cleared for \
factory builds, and the KITS (deep, certified domain packs). Be honest about \
all of it and never invent an id.
- Kits: when no kit covers the user's business, say so in your own words \
along the lines of: we do not have a top-notch kit for that in the store \
today, but we can build a simple one for you now; it will take longer and \
may cost more than a platform built on a ready kit. Say it once, early, \
in the "message" of your first ask_user or reply. Never imply a kit exists \
when it does not.
- Connectors and MCP: ask which outside systems they already use (accounting, \
booking / PMS, ERP, drives, email, messaging) when the brief does not say. On \
draft_platform, put the ids of attachable CONNECTORS / MCP parts the platform \
needs in "connectors" — only ids from those two lists. Put every system the \
user named that is NOT attachable (not in the store, or not cleared) in \
"missing_connectors" by its plain name, and tell the user plainly that it \
will ship as a marked placeholder until it is built — never as working.

Return ONLY JSON: {"action": "...", "brief": "", "refine_message": "", "message": "", "connectors": [], "missing_connectors": []}.
"""


def _llm_json_call(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Floor chat JSON call — uses ``CEREBRUM_CHAT_LLM_*``, never Cursor completions."""
    return _architect_llm_json_call(messages, use_chat_config=True)


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
    cfg = get_llm_config()
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
    # Exact Approve gate is deterministic. Calling the LLM here is how
    # empty OpenRouter completions became CEREBRUMDEV-BACKEND-F/G.
    if platform_chat_flow.has_pending_blueprint(state) and platform_chat_flow.is_exact_approve_gate(
        message
    ):
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
    elif platform_chat_flow.is_handoff_awaiting_n3(state):
        lines.append(
            "Cli-pivot handed off to N3 (HANDOFF_TO_N3). continue / "
            "start_coder MUST ingest the cerebrum-builds store-gate "
            "12/12 commit status. Do NOT draft a new platform, do NOT "
            "re-enter WRITER, and do NOT launch another Background Agent."
        )
    elif platform_chat_flow.is_generation_complete(state):
        lines.append(
            "Code-phase 5/5 SUCCEEDED but the platform is NOT pilot-ready "
            "(pytest -m pilot / Store ops still open). continue/resume MUST "
            "call start_coder — that opens a pilot cycle on the same hash. "
            "Do not draft a new platform."
        )
    elif platform_chat_flow.is_generation_terminal_failure(state):
        lines.append(
            "Last coding run FAILED (rework exhausted or TESTER still red). "
            "That workspace is dead — do NOT resume it and do NOT say "
            "'same blueprint hash — not starting over'. A new platform brief "
            "MUST call draft_platform. continue / try again / start_coder "
            "starts a FRESH workspace with a reset rework budget."
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


def _conversation(state: Any, message: str) -> str:
    """The recent dialogue, oldest first, without the message being decided.

    chat.py appends the user turn before routing, so the current message is
    usually the last history entry -- drop it rather than show it twice.
    """
    history = list(getattr(state, "chat_history", None) or [])
    if history and isinstance(history[-1], dict):
        last = history[-1]
        if (
            last.get("role") == "user"
            and str(last.get("content") or "").strip() == (message or "").strip()
        ):
            history = history[:-1]
    lines: List[str] = []
    for turn in history[-_HISTORY_TURNS:]:
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("content") or "").strip()
        if not text:
            continue
        who = "User" if turn.get("role") == "user" else "Floor"
        lines.append(f"{who}: {text[:_HISTORY_TURN_CHARS]}")
    return "\n".join(lines) or "(this is the first message)"


def _store_inventory() -> str:
    """The Store as the chat may describe it -- so it can be honest."""
    try:
        from app.factory.store_catalog import render_for_chat, store_catalog

        return render_for_chat(store_catalog())
    except Exception:  # noqa: BLE001 -- inventory is context, never a blocker
        logger.warning("Floor chat: Store inventory unavailable", exc_info=True)
        return (
            "STORE INVENTORY: unavailable right now — do not claim any "
            "ready-made part, connector or kit."
        )


def _elicitation_facts(state: Any) -> str:
    pd = getattr(state, "product_design", None)
    asked = int(getattr(pd, "elicitation_rounds", 0) or 0)
    left = max(0, MAX_ELICITATION_ROUNDS - asked)
    if left == 0:
        return "Question rounds remaining: 0 — ask_user is forbidden; draft now."
    return f"Question rounds remaining: {left}."


def _str_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, (str, int)) and str(x).strip()][:12]


def decide(state: Any, message: str) -> Dict[str, Any]:
    """Ask the factory LLM which Floor action to take. Raises on failure."""
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                _session_facts(state)
                + " "
                + _elicitation_facts(state)
                + "\n"
                + _store_inventory()
                + "\n\nConversation so far:\n"
                + _conversation(state, message)
                + "\n\nUser message:\n"
                + (message or "")
            ),
        },
    ]
    data = _llm_json_call(messages)
    if not isinstance(data, dict):
        raise ValueError("Floor chat LLM returned a non-object")
    action = str(data.get("action") or "").strip()
    if not action:
        raise LlmSoftMiss("Floor chat LLM returned empty action")
    if action not in _ACTIONS:
        raise ValueError(f"Floor chat LLM returned unknown action {action!r}")
    return {
        "action": action,
        "brief": str(data.get("brief") or "").strip(),
        "refine_message": str(data.get("refine_message") or "").strip(),
        "message": str(data.get("message") or "").strip(),
        "connectors": _str_list(data.get("connectors")),
        "missing_connectors": _str_list(data.get("missing_connectors")),
    }


def try_decide(state: Any, message: str) -> Optional[Dict[str, Any]]:
    """Decide, or None on miss/failure. Soft misses log warning, not error.

    Sentry LoggingIntegration turns ``logger.exception`` into error events.
    Empty OpenRouter completions are expected; do not page on them.
    """
    try:
        return decide(state, message)
    except LlmSoftMiss as exc:
        logger.warning("Floor chat LLM miss; falling back to regex routing: %s", exc)
        return None
    except Exception:
        logger.exception("Floor chat LLM failed; falling back to regex routing")
        return None


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
        or platform_chat_flow.is_generation_terminal_failure(state)
    ):
        return {
            "action": "start_coder",
            "brief": "",
            "refine_message": "",
            "message": "",
            "coerced": True,
        }
    return decision


def _elicitation_allowed(state: Any) -> bool:
    """Questions belong before the first draft, and only a bounded number."""
    pd = getattr(state, "product_design", None)
    if pd is None:
        return False
    if getattr(pd, "blueprint", None) or getattr(pd, "generation", None):
        return False
    return int(getattr(pd, "elicitation_rounds", 0) or 0) < MAX_ELICITATION_ROUNDS


def enforce_elicitation_cap(
    decision: Dict[str, Any], state: Any, message: str
) -> Dict[str, Any]:
    """An ask_user the session cannot afford becomes a draft.

    The model is told the limit; this is what makes it true. Without it a
    model that keeps finding one more question strands the user in a
    conversation that never reaches a blueprint.
    """
    if decision.get("action") != "ask_user":
        return decision
    if _elicitation_allowed(state) and decision.get("message"):
        return decision
    return {
        "action": "draft_platform",
        "brief": decision.get("brief") or "",
        "refine_message": "",
        "message": "",
        "connectors": list(decision.get("connectors") or []),
        "missing_connectors": list(decision.get("missing_connectors") or []),
        "coerced": True,
    }


def _consolidated_brief(state: Any, message: str, model_brief: str) -> str:
    """The brief the architect drafts from: the model's restatement PLUS
    the user's own words.

    The restatement is a summary, and a summary can drop the one answer
    that mattered. The verbatim turns ride along so nothing the user said
    during the questions is lost to paraphrase.
    """
    pd = getattr(state, "product_design", None)
    said = [s for s in (getattr(pd, "elicitation_turns", None) or []) if s.strip()]
    if not said:
        return model_brief or message
    if (message or "").strip() and message.strip() not in said:
        said = [*said, message.strip()]
    head = (model_brief or said[0]).strip()
    return (
        head
        + "\n\nIn the user's own words:\n"
        + "\n".join(f"- {s}" for s in said)
    )


def _attach_connectors(
    state: Any, result: Dict[str, Any], chosen: List[str], missing: List[str]
) -> None:
    """Bind the connectors the conversation settled on to the drafted blueprint.

    An attachable Store part becomes a REUSE capability, so CLONER really
    clones it. A system the Store cannot supply goes to
    ``blueprint.connectors``, which the generator emits as a marked
    ``not_implemented`` placeholder -- present and honest, never working by
    implication. The model's ids are not trusted: anything that is not an
    attachable part in the catalog is treated as missing.
    """
    if not chosen and not missing:
        return
    import re

    from app.factory.blueprint import ProductBlueprint
    from app.factory.store_catalog import offerable_connector_ids, store_catalog

    pd = state.product_design
    if not isinstance(pd.blueprint, dict):
        return
    try:
        offerable = set(offerable_connector_ids(store_catalog()))
    except Exception:  # noqa: BLE001
        logger.warning("Floor chat: catalog unavailable; no connectors attached", exc_info=True)
        return
    attach = [c for c in dict.fromkeys(chosen) if c in offerable]
    placeholders = [*missing, *[c for c in chosen if c not in offerable]]
    slugs = list(
        dict.fromkeys(
            s
            for s in (re.sub(r"[^a-z0-9_]+", "_", m.lower()).strip("_") for m in placeholders)
            if s and s not in offerable
        )
    )

    bp = dict(pd.blueprint)
    caps = [dict(c) for c in (bp.get("capabilities") or []) if isinstance(c, dict)]
    bound = {b for c in caps for b in (c.get("block_ids") or [])}
    added: List[str] = []
    for cid in attach:
        if cid in bound:
            continue
        caps.append(platform_chat_flow._capability_for_id(cid, sorted(offerable)))
        added.append(cid)
    bp["capabilities"] = caps
    bp["connectors"] = list(dict.fromkeys([*(bp.get("connectors") or []), *slugs]))
    try:
        validated = ProductBlueprint.model_validate(bp)
    except Exception:  # noqa: BLE001 -- a bad attach must not cost the draft
        logger.warning("Floor chat: connector attach rejected by the blueprint", exc_info=True)
        return
    pd.blueprint = validated.model_dump(mode="json")
    result["blueprint"] = pd.blueprint
    try:
        from app.factory.blueprint import blueprint_to_yaml

        result["yaml"] = blueprint_to_yaml(validated)
    except Exception:  # noqa: BLE001
        pass
    notes: List[str] = []
    if added:
        notes.append("Connectors from the store: " + ", ".join(added) + ".")
    if slugs:
        notes.append(
            "Not available ready-made, so shipped as marked placeholders until built: "
            + ", ".join(slugs)
            + "."
        )
    if notes and isinstance(result.get("summary"), str):
        result["summary"] = result["summary"].rstrip() + " " + " ".join(notes)


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

    if action == "ask_user":
        pd = state.product_design
        if (message or "").strip():
            pd.elicitation_turns = [*pd.elicitation_turns, message.strip()]
        pd.elicitation_rounds = int(pd.elicitation_rounds or 0) + 1
        return {
            "sse": "info",
            "ok": True,
            "summary": decision.get("message") or "",
            "stream_delta": True,
            "elicitation": True,
        }

    if action == "draft_platform":
        brief = _consolidated_brief(state, message, decision.get("brief") or "")
        result = platform_chat_flow.draft_from_chat(state, brief)
        state.product_design.elicitation_turns = []
        state.product_design.elicitation_rounds = 0
        _attach_connectors(
            state,
            result,
            list(decision.get("connectors") or []),
            list(decision.get("missing_connectors") or []),
        )
        # The model's own words ride along with the draft: that is where it
        # says "no top-notch kit for this, a simple one takes longer and may
        # cost more". Dropping them made a draft look like a silent switch.
        said = str(decision.get("message") or "").strip()
        if said and isinstance(result.get("summary"), str):
            result["summary"] = said + " " + result["summary"]
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
    decision = try_decide(state, message)
    if decision is None:
        return None
    decision = coerce_explicit_approval(decision, state, message)
    decision = enforce_elicitation_cap(decision, state, message)
    return apply_decision(state, message, decision)
