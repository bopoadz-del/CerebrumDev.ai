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
    3. When a factory LLM key is configured, the chat LLM decides the action
     (draft / start_coder / reply). start_coder is the only chat door that
     launches the WRITER coding agent. Regex approval ("approve") is the
     offline fallback when the LLM is unset or the call fails.
    4. Approval starts WRITER when a blueprint is pending. continue/resume
     resumes an in-flight or interrupted (non-terminal) run even after the
     blueprint is already approved — a pending unapproved blueprint is not
     required. After code-phase 5/5 SUCCESS, continue opens a pilot cycle
     on the same workspace (pytest -m pilot + STORE ops), not a new product.
     A RUN_FAILED / rework-exhausted ledger is terminal: same-hash continue
     or a new brief must start a fresh workspace (reset rework budget),
     never a no-op resume of the dead run.
    5. Kit-configurator vocabulary (chain/blocks/kits/domain/lora/...) stays
     in the legacy chat flow even when it also mentions a platform noun.
    6. Anything else falls through to the normal kit-configurator chat.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from .blueprint import CapabilitySpec, ProductBlueprint
from .dual_registry import dual_registered_ids
from .blocks_source import resolve_blocks_root
from .paths import factory_outputs_root
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
    r"[\w\s,.'\-/: ]{0,100}?"
    r"\b(platform|product|system|portal)\b",
    re.IGNORECASE,
)

# Live Floor testers describe a business ("build me a tasting room for a
# winery") without saying "platform". That is still a product brief.
_BUSINESS_BRIEF_RE = re.compile(
    r"\b(build|create|make|generate|assemble|design|spin\s*up|set\s*up|"
    r"i\s+(?:want|need)|give\s*me|get\s*me)\b"
    r".{0,100}\bfor\b.{2,80}",
    re.IGNORECASE | re.DOTALL,
)

# Kit-configurator vocabulary: only the configurator dialect, not ordinary
# English ("my hospitality domain", "reservation blocks"). Bare \bdomain\b /
# \bblock\b used to dump product briefs into the legacy chain generator,
# and the Floor then overwrote the reply with "kit configuration".
_KIT_CONFIG_RE = re.compile(
    r"("
    r"\b(lora|learning\s*rate|vector\s*db|hnsw)\b"
    r"|\b(add|remove|show|list|use)\s+(?:the\s+)?(block|blocks|kit|kits|domain|chain)\b"
    r"|\b(chain|kit)\s+(with|blocks|block)\b"
    r"|\bproduct\s+chain\b"
    r"|\bwhat blocks\b"
    r"|\bblocks are available\b"
    r"|\buse domain\b"
    r"|\bthe retail domain\b"
    r"|\bretail kit\b"
    r")",
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

# "continue" / "resume" after takeover. Must NOT require a pending
# (unapproved) blueprint — that is the live Floor hole: after start_coder
# the blueprint is approved and generation is mid-flight or interrupted,
# and the chat LLM was told start_coder is forbidden.
_RESUME_RE = re.compile(
    r"^\s*(?:please\s+)?"
    r"(continue|resume|keep\s+going|pick\s+up(?:\s+where\s+you\s+left\s+off)?"
    r"|start\s+the\s+coder|resume\s+the\s+coder)"
    r"(?:\s+please)?\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Floor "run the pilot" after code-phase 5/5. Same workspace — not a new draft.
_PILOT_RE = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:run\s+(?:the\s+)?pilot(?:\s+gate|\s+cycle)?|"
    r"make\s+it\s+pilot(?:-?\s*ready)?|"
    r"pilot(?:-?\s*ready)?|"
    r"continue\s+(?:to\s+)?pilot|"
    r"resume\s+(?:to\s+)?pilot)"
    r"(?:\s+please)?\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_kit_config_vocabulary(message: str) -> bool:
    """True when the message is about chains/blocks/kits, not a product brief."""
    return bool(_KIT_CONFIG_RE.search(message or ""))


def is_platform_intent(message: str) -> bool:
    """True when the message asks the factory to create a platform/product."""
    text = message or ""
    if is_kit_config_vocabulary(text):
        return False
    if _PLATFORM_INTENT_RE.search(text):
        return True
    return bool(_BUSINESS_BRIEF_RE.search(text))


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


def is_resume_request(message: str) -> bool:
    """True when the user asked to continue/resume an existing coding run."""
    return bool(_RESUME_RE.match((message or "").strip()))


def is_pilot_request(message: str) -> bool:
    """True when the user asked to run the Store-green / pilot cycle."""
    return bool(_PILOT_RE.match((message or "").strip()))


# --- State helpers ----------------------------------------------------------

def has_pending_blueprint(state: Any) -> bool:
    pd = getattr(state, "product_design", None)
    return bool(pd and pd.blueprint and not pd.blueprint_approved)


def _blocks_root() -> Optional[Path]:
    """Thin alias for the shared resolver (kept so existing monkeypatches and
    call sites stay valid). See app.factory.blocks_source for the fix history:
    every generation door — chat AND the HTTP plan/generate routes — must use
    the same resolution or products differ in fidelity by entry point."""
    return resolve_blocks_root()


def _session_output(session_id: str, product_id: str, output_root: Optional[Path]) -> Path:
    if output_root is not None:
        return Path(output_root) / product_id
    return factory_outputs_root() / "sessions" / session_id / product_id


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
    if pd.blueprint_approved:
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

    from app.factory.build.intake_blueprint import (
        chat_turns_from_session,
        intake_from_product_blueprint,
        render_plain_language,
    )
    from app.factory.build.brief_compiler import synthesize_domain_pack

    turns = chat_turns_from_session(state)
    if not turns and message.strip():
        turns = [{"turn": 1, "role": "user", "text": message.strip()}]
    try:
        intake = intake_from_product_blueprint(
            bp,
            plan=None,
            chat_turns=turns,
            brief=message,
            domain_pack=synthesize_domain_pack(bp, type("P", (), {"capabilities": bp.capabilities})()),
        )
        pd.intake_blueprint = intake
        plain = render_plain_language(intake)
    except Exception:  # noqa: BLE001 — draft must still park the product blueprint
        pd.intake_blueprint = None
        plain = ""

    capabilities = [c.id for c in bp.capabilities]
    blocks = sorted({b for c in bp.capabilities for b in c.block_ids})
    if bp.product_id == "cerebrum-steward":
        source = "golden_steward"
    elif bp.drafting_mode == "golden_lettings":
        source = "golden_lettings"
    else:
        source = "drafted"
    # Say who drafted it. A dead LLM key must not look identical to a
    # working architect.
    mode_labels = {
        "architect_llm": "Drafted by the architect LLM.",
        "golden_steward": "Drafted from the golden steward blueprint.",
        "golden_lettings": "Drafted from the golden residential-lettings blueprint.",
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
        "intake_blueprint": pd.intake_blueprint,
        "plain_language": plain,
    }


def _compile_and_lint_approved(state: Any, bp: ProductBlueprint) -> Dict[str, Any]:
    """Approve is the only Floor event that opens compile → lint → session.

    Spend-gated by construction: a rejected brief never starts generate.
    """
    from app.factory.build.brief_compiler import compile_brief, verify_inventory
    from app.factory.build.brief_lint import lint_brief
    from app.factory.build.intake_blueprint import (
        chat_turns_from_session,
        render_plain_language,
    )

    plan = plan_blueprint(bp, blocks_root=_blocks_root())
    turns = chat_turns_from_session(state)
    compiled = compile_brief(
        bp,
        plan,
        chat_turns=turns,
        brief=str(getattr(state.product_design, "brief", "") or ""),
        intake=getattr(state.product_design, "intake_blueprint", None),
    )
    from app.factory.build.brief_compiler import InventoryHalt

    try:
        verify_inventory(compiled)
    except InventoryHalt as exc:
        pd = state.product_design
        pd.intake_blueprint = compiled.intake
        pd.brief_lint = {
            "ok": False,
            "errors": [str(exc)],
            "checks": {"inventory_halt": True},
        }
        pd.plan = plan.to_dict()
        class _Halt:
            ok = False
            errors = [str(exc)]

            def to_dict(self):
                return pd.brief_lint

        return {
            "compiled": compiled,
            "lint": _Halt(),
            "plan": plan,
            "plain_language": render_plain_language(compiled.intake),
        }
    lint = lint_brief(compiled)
    pd = state.product_design
    pd.intake_blueprint = compiled.intake
    pd.brief_lint = lint.to_dict()
    pd.plan = plan.to_dict()
    return {
        "compiled": compiled,
        "lint": lint,
        "plan": plan,
        "plain_language": render_plain_language(compiled.intake),
    }


def approve_and_generate(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_approve",
) -> Dict[str, Any]:
    """Approve the pending blueprint, compile+lint the brief, then generate.

    Mutates state.product_design exactly like POST /product/approve +
    /product/generate. Returns the generation payload.

    ``triggered_by`` is provenance for the chat door: ``chat_llm`` when the
    Floor LLM called start_coder, ``regex_approve`` when the offline keyword
    path ran. The coding agent still lives only in WRITER.

    Approve is the only event that opens compile → lint → coder session.
    A BRIEF_LINT_REJECTED brief never starts generate.
    """
    pd = state.product_design
    if not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")

    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    gated = _compile_and_lint_approved(state, bp)
    lint = gated["lint"]
    if not lint.ok:
        pd.last_error = "BRIEF_LINT_REJECTED: " + "; ".join(lint.errors)
        return {
            "ok": False,
            "sse": "error",
            "summary": (
                "Brief rejected — coding session never opened. "
                + pd.last_error
            ),
            "brief_lint": lint.to_dict(),
            "plain_language": gated.get("plain_language"),
            "blueprint_approved": True,
        }
    if not pd.plan:
        pd.plan = gated["plan"].to_dict()

    if has_running_build(state) or _live_build_thread(bp.product_id) is not None:
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply

    out = _session_output(state.session_id, bp.product_id, output_root)
    result = generate_product(
        bp,
        out,
        blocks_root=_blocks_root(),
        quota_account_id=getattr(state, "user_id", None),
    )
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply
    _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
    pd.last_error = None

    trigger_line = (
        "The chat LLM started the coding agent. "
        if triggered_by == "chat_llm"
        else ""
    )

    # A runner build has STARTED, not finished. Saying "product generated —
    # download it" here would be a lie the customer discovers as a 409 on the
    # download, so the runner engine gets its own honest message.
    if result.get("engine") == "runner":
        caps = len((pd.plan or {}).get("capabilities", []) or [])
        from app.factory.build.auto_pilot import factory_auto_pilot_enabled

        if factory_auto_pilot_enabled():
            expect = (
                "This is a Store-green run: code cycle, then a pilot cycle "
                "(pytest -m pilot and WRITER rework) on the same workspace. "
                "Watch it here — Finished / Download ready unlocks only when "
                "the platform is pilot-ready."
            )
        else:
            expect = (
                "This is a code-cycle pass (pytest -m 'not pilot'). "
                "A SUCCESS here is a prototype, not pilot-ready. "
                "The download will be labeled as a code-cycle prototype."
            )
        return {
            "ok": True,
            "generation": pd.generation,
            "plan": pd.plan,
            "triggered_by": triggered_by,
            "summary": (
                f"{trigger_line}Build started for {result['product_id']}: the coding agent "
                "has taken over the floor and is "
                f"writing {caps} capability(ies) against the real block contracts. "
                + expect
            ),
        }

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
        "triggered_by": triggered_by,
        "summary": trigger_line + summary,
    }


def has_running_build(state: Any) -> bool:
    """True while the coding agent is manufacturing the approved feature list."""
    pd = getattr(state, "product_design", None)
    gen = getattr(pd, "generation", None) if pd else None
    if not gen or gen.get("engine") != "runner":
        return False
    out = gen.get("output_dir")
    if not out:
        return False
    from app.factory.build_jobs import build_status

    return build_status(out).get("state") == "building"


def running_build_reply(state: Any) -> Dict[str, Any]:
    """Grounded status while the coding agent owns the floor."""
    pd = state.product_design
    gen = pd.generation or {}
    from app.factory.build_jobs import build_status

    st = build_status(gen.get("output_dir") or "")
    activity = st.get("activity") or "writing the platform"
    done = st.get("phases_done") or 0
    total = st.get("phases_total") or 5
    summary = (
        f"The coding agent has taken over. It is writing {gen.get('product_id')} "
        f"— {done}/{total} phases ({activity}). Confirmations are already in: "
        "wait for the gates, or watch progress on Your Platforms."
    )
    return {
        "ok": True,
        "sse": "info",
        "summary": summary,
        "stream_delta": True,
        "build": st,
    }


# --- Resume an in-flight or interrupted coding run --------------------------


def _generation_output_dir(state: Any, output_root: Optional[Path] = None) -> Optional[Path]:
    """Where this session's coding run writes. Survives a worker restart."""
    pd = getattr(state, "product_design", None)
    if not pd:
        return None
    gen = getattr(pd, "generation", None) or {}
    out = gen.get("output_dir")
    if out:
        return Path(out)
    bp = pd.blueprint or {}
    product_id = gen.get("product_id") or bp.get("product_id")
    session_id = getattr(state, "session_id", None)
    if product_id and session_id:
        return _session_output(session_id, product_id, output_root)
    return None


def _ledger_for(output_dir: Path):
    from app.factory.build.ledger import BuildLedger

    return BuildLedger(Path(output_dir) / "build_ledger.jsonl")


def _live_build_thread(product_id: str):
    """The in-process runner thread for this product, if this worker still has it.

    After a deploy / disconnect the thread is gone even while the ledger
    still reads "building". That is the case continue must restart.
    """
    import threading

    name = f"build-{product_id}"
    for thread in threading.enumerate():
        if thread.name == name and thread.is_alive():
            return thread
    return None


def _generation_status(state: Any, output_root: Optional[Path] = None) -> Dict[str, Any]:
    """Live ledger status, falling back to the persisted generation snapshot."""
    pd = getattr(state, "product_design", None)
    gen = getattr(pd, "generation", None) if pd else None
    if not gen:
        return {}
    out = _generation_output_dir(state, output_root)
    if out:
        from app.factory.build_jobs import build_status

        st = build_status(out)
        if st.get("state") != "unknown":
            return st
    persisted = gen.get("build")
    return dict(persisted) if isinstance(persisted, dict) else {}


def _ledger_resume_point(state: Any, output_root: Optional[Path] = None) -> Optional[str]:
    out = _generation_output_dir(state, output_root)
    if not out:
        return None
    try:
        ledger = _ledger_for(out)
        if not ledger.exists():
            return None
        point = ledger.resume_point()
        return point.value if point else None
    except Exception:  # noqa: BLE001 — a torn ledger must not block chat
        return None


def _artifacts_remain(status: Dict[str, Any]) -> bool:
    """True when WRITER progress is incomplete (the live 22/28 case)."""
    done = status.get("activity_done")
    total = status.get("activity_total")
    try:
        if total is not None and done is not None and int(done) < int(total):
            return True
    except (TypeError, ValueError):
        pass
    auth = status.get("authorship") or {}
    artifacts = auth.get("artifacts") or 0
    written = auth.get("agent_written") or 0
    return bool(artifacts) and written < artifacts


def is_generation_complete(state: Any) -> bool:
    """True only when the last coding run recorded SUCCESS."""
    st = _generation_status(state)
    if st.get("state") == "succeeded":
        return True
    pd = getattr(state, "product_design", None)
    gen = getattr(pd, "generation", None) if pd else None
    if not gen:
        return False
    out = _generation_output_dir(state)
    if not out:
        return False
    try:
        return bool(_ledger_for(out).succeeded())
    except Exception:  # noqa: BLE001
        return False


def is_generation_terminal_failure(
    state: Any, output_root: Optional[Path] = None
) -> bool:
    """True when the last coding run recorded RUN_FAILED (incl. rework exhausted).

    A terminal failure is not an interrupted run. Resume would attach to a
    dead ledger (TESTER still red, rework spent) and the Floor would stay
    CODING AGENT STOPPED. Callers must start a fresh workspace instead.
    """
    st = _generation_status(state, output_root)
    if st.get("state") == "failed":
        return True
    out = _generation_output_dir(state, output_root)
    if not out:
        return False
    try:
        from app.factory.build.ledger import EventKind

        event = _ledger_for(out).terminal_event()
        return event is not None and event.kind is EventKind.RUN_FAILED
    except Exception:  # noqa: BLE001
        return False


def is_generation_resumable(state: Any) -> bool:
    """True when a coding run exists that is interrupted, not finished.

    Does not require a pending (unapproved) blueprint. After takeover the
    blueprint is approved and the runner workspace / ledger is the resume
    source — the same-hash path ``POST .../product/generate`` already uses.

    A RUN_FAILED / rework-exhausted ledger is terminal, not resumable.
    """
    pd = getattr(state, "product_design", None)
    if not pd or not getattr(pd, "blueprint", None):
        return False
    if is_generation_complete(state):
        return False
    if is_generation_terminal_failure(state):
        return False
    gen = getattr(pd, "generation", None) or {}
    if not gen:
        return False
    out = _generation_output_dir(state)
    if out:
        try:
            ledger = _ledger_for(out)
            if ledger.exists() and not ledger.succeeded():
                from app.factory.build.ledger import EventKind

                term = ledger.terminal_event()
                if term is not None and term.kind is EventKind.RUN_FAILED:
                    return False
                return True
        except Exception:  # noqa: BLE001
            pass
        if (Path(out) / "build_ledger.jsonl").is_file():
            return not is_generation_terminal_failure(state)
        if Path(out).is_dir() and any(Path(out).iterdir()):
            return True
    st = _generation_status(state)
    if st.get("state") in {"building", "stalled"}:
        return True
    if _artifacts_remain(st):
        return True
    return bool(gen.get("output_dir") or gen.get("inputs_hash"))


def _record_generation(
    pd: Any,
    result: Dict[str, Any],
    *,
    triggered_by: str,
    resumed: bool,
) -> None:
    """Persist enough for a new uvicorn worker to resume the same run."""
    from app.factory.build_jobs import build_status

    out = result.get("output_dir") or ""
    st = result.get("build") if isinstance(result.get("build"), dict) else None
    if out and (st is None or st.get("state") == "unknown"):
        st = build_status(out)
    st = st or {}
    resume_point = None
    if out:
        try:
            point = _ledger_for(Path(out)).resume_point()
            resume_point = point.value if point else None
        except Exception:  # noqa: BLE001
            resume_point = None
    pd.generation = {
        "output_dir": result.get("output_dir"),
        "inputs_hash": result.get("inputs_hash"),
        "product_id": result.get("product_id"),
        "coder": result.get("coder"),
        "engine": result.get("engine"),
        "build": st,
        "phases_done": st.get("phases_done"),
        "resume_point": resume_point,
        "triggered_by": triggered_by,
        "resumed": resumed,
    }


def _resume_cycle(state: Any, output_root: Optional[Path] = None) -> str:
    """Resume a failed/open pilot as pilot, not as a fresh code cycle."""
    out = _generation_output_dir(state, output_root)
    if not out:
        return "code"
    try:
        ledger = _ledger_for(out)
        if ledger.pilot_ready():
            return "code"
        if ledger.pilot_cycle_open():
            return "pilot"
        from app.factory.build.ledger import EventKind

        if any(e.kind is EventKind.PILOT_OPENED for e in ledger.events()):
            return "pilot"
    except Exception:  # noqa: BLE001
        return "code"
    return "code"


def is_pilot_ready(state: Any, output_root: Optional[Path] = None) -> bool:
    """True only after a SUCCESS that closed a Store-green / pilot cycle."""
    out = _generation_output_dir(state, output_root)
    if not out:
        return False
    try:
        return bool(_ledger_for(out).pilot_ready())
    except Exception:  # noqa: BLE001
        return False


def already_complete_reply(state: Any) -> Dict[str, Any]:
    """Honest answer when continue is typed after a finished run.

    Pilot-ready (Store-green) is the only terminal that refuses another
    coding run. Code-phase 5/5 still has a pilot cycle to open — callers
    should use ``resume_pilot_cycle`` instead of this reply.
    """
    pd = state.product_design
    gen = pd.generation or {}
    product = gen.get("product_id") or (pd.blueprint or {}).get("product_name") or "this product"
    st = _generation_status(state)
    done = st.get("phases_done")
    total = st.get("phases_total")
    phase = f" ({done}/{total} phases)" if done is not None and total is not None else ""
    if is_pilot_ready(state):
        summary = (
            f"{product} is already pilot-ready (Store-green){phase}. "
            "I did not start a new coding run. Download it from Your Platforms."
        )
        return {
            "ok": True,
            "sse": "info",
            "summary": summary,
            "stream_delta": True,
            "already_complete": True,
            "pilot_ready": True,
            "generation": gen,
            "build": st,
        }
    summary = (
        f"{product} finished the code-phase 5/5{phase}. "
        "It is not yet pilot-ready (pytest -m pilot / Store ops). "
        "Say continue to open a pilot cycle on the same workspace."
    )
    return {
        "ok": True,
        "sse": "info",
        "summary": summary,
        "stream_delta": True,
        "already_complete": True,
        "pilot_ready": False,
        "generation": gen,
        "build": st,
    }


def start_fresh_generation(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_fresh",
) -> Dict[str, Any]:
    """Start a new auto-pilot cycle on a new workspace after a terminal failure.

    Same blueprint hash is allowed — the previous RUN_FAILED / rework-
    exhausted ledger is not a resume source. The new dir gets a reset
    rework budget so #287 auto-pilot and #288 payload contracts can run.
    """
    pd = state.product_design
    if not pd or not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    if is_pilot_ready(state, output_root):
        return already_complete_reply(state)
    if has_running_build(state):
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply

    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()

    live = _live_build_thread(bp.product_id)
    if live is not None:
        st = _generation_status(state, output_root)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": st,
        }

    from app.factory.build_jobs import next_fresh_output

    prior = _generation_output_dir(state, output_root)
    base = prior or _session_output(state.session_id, bp.product_id, output_root)
    out = next_fresh_output(base)
    prior_hash = (pd.generation or {}).get("inputs_hash")
    result = generate_product(
        bp,
        out,
        blocks_root=_blocks_root(),
        cycle="code",
        quota_account_id=getattr(state, "user_id", None),
    )
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply
    _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
    if prior_hash and result.get("inputs_hash") and result["inputs_hash"] != prior_hash:
        logger.warning(
            "fresh-start hash changed for %s: was %s now %s",
            bp.product_id,
            prior_hash[:12],
            str(result["inputs_hash"])[:12],
        )
    pd.last_error = None
    prior_dir = str(prior) if prior else None
    new_dir = result.get("output_dir")
    summary = (
        f"Starting a fresh build for {result['product_id']} on a new workspace. "
        "The previous run failed (rework exhausted or gates still red) and "
        "will not be resumed — the rework budget is reset. This is not a "
        "same-hash resume."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": False,
        "fresh": True,
        "fresh_workspace": True,
        "prior_output_dir": prior_dir,
        "output_dir": new_dir,
        "stream_delta": False,
        "summary": summary,
        "build": result.get("build"),
    }


def resume_generation(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_resume",
) -> Dict[str, Any]:
    """Resume the session's coding run on the same blueprint hash.

    Calls ``generate_product`` into the existing output dir. The runner
    sees the ledger, skips completed phases, and does not re-CLONER from
    zero when WRITER/TESTER already progressed.

    A terminal RUN_FAILED is not resumed — that path starts a fresh
    workspace so a dead ledger cannot swallow the request.
    """
    pd = state.product_design
    if not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    if is_generation_complete(state):
        return already_complete_reply(state)
    if is_generation_terminal_failure(state, output_root):
        resume_by = (
            "chat_llm" if triggered_by == "chat_llm" else "regex_fresh"
        )
        return start_fresh_generation(
            state, output_root=output_root, triggered_by=resume_by
        )

    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()

    out = _generation_output_dir(state, output_root)
    if out is None:
        out = _session_output(state.session_id, bp.product_id, output_root)

    live = _live_build_thread(bp.product_id)
    if live is not None:
        st = _generation_status(state, output_root)
        activity = st.get("activity") or "writing the platform"
        done = st.get("phases_done") or 0
        total = st.get("phases_total") or 5
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id} — "
                f"{done}/{total} phases ({activity}). I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": st,
        }

    prior_hash = (pd.generation or {}).get("inputs_hash")
    result = generate_product(
        bp,
        out,
        blocks_root=_blocks_root(),
        cycle=_resume_cycle(state, output_root),
        quota_account_id=getattr(state, "user_id", None),
    )
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": result.get("build"),
        }
    _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
    if prior_hash and result.get("inputs_hash") and result["inputs_hash"] != prior_hash:
        logger.warning(
            "resume hash changed for %s: was %s now %s",
            bp.product_id,
            prior_hash[:12],
            str(result["inputs_hash"])[:12],
        )
    pd.last_error = None

    st = (pd.generation or {}).get("build") or {}
    resume_at = (pd.generation or {}).get("resume_point") or st.get("activity") or "the last phase"
    done = st.get("phases_done") or 0
    total = st.get("phases_total") or 5
    written = st.get("activity_done")
    of = st.get("activity_total")
    artifact_bit = ""
    if written is not None and of is not None:
        artifact_bit = f", {written}/{of} artifacts"
    summary = (
        f"Resuming the coding agent for {result['product_id']} from {resume_at} "
        f"({done}/{total} phases{artifact_bit}). Same blueprint hash — not starting over."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": True,
        "stream_delta": False,
        "summary": summary,
    }


def resume_pilot_cycle(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_pilot",
) -> Dict[str, Any]:
    """Open a Store-green cycle on the same workspace / hash / output.

    Does not draft or approve a new blueprint. The runner sees
    ``PILOT_OPENED``, skips COLLECTOR/CLONER/WRITER, runs ``pytest -m pilot``,
    reworks only failing capabilities, and applies STORE ops.
    """
    if is_pilot_ready(state, output_root):
        return already_complete_reply(state)
    pd = state.product_design
    if not pd or not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()
    out = _generation_output_dir(state, output_root)
    if out is None:
        out = _session_output(state.session_id, bp.product_id, output_root)
    live = _live_build_thread(bp.product_id)
    if live is not None:
        st = _generation_status(state, output_root)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": st,
        }
    prior_hash = (pd.generation or {}).get("inputs_hash")
    result = generate_product(
        bp,
        out,
        blocks_root=_blocks_root(),
        cycle="pilot",
        quota_account_id=getattr(state, "user_id", None),
    )
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": result.get("build"),
        }
    _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
    if prior_hash and result.get("inputs_hash") and result["inputs_hash"] != prior_hash:
        logger.warning(
            "pilot resume hash changed for %s: was %s now %s",
            bp.product_id,
            prior_hash[:12],
            str(result["inputs_hash"])[:12],
        )
    st = result.get("build") or {}
    summary = (
        f"Opening a pilot cycle for {result['product_id']} on the same "
        "workspace/hash. TESTER will run pytest -m pilot; WRITER reworks "
        "only failing capabilities; STORE_MANAGER applies store ops. "
        "Not a new product."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": True,
        "cycle": "pilot",
        "stream_delta": False,
        "summary": summary,
        "build": st,
    }


def start_or_resume_coder(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_approve",
) -> Dict[str, Any]:
    """Start WRITER on a pending blueprint, or resume an interrupted run.

    Never requires a pending unapproved blueprint to resume. Code-phase
    SUCCESS is not the end: continue opens a pilot cycle on the same
    workspace. Only a Store-green SUCCESS refuses another run.

    A RUN_FAILED / rework-exhausted ledger is terminal: continue or
    start_coder starts a fresh workspace with a reset rework budget.
    """
    if has_pending_blueprint(state):
        return approve_and_generate(
            state, output_root=output_root, triggered_by=triggered_by
        )
    if is_pilot_ready(state, output_root):
        return already_complete_reply(state)
    if is_generation_complete(state):
        resume_by = (
            "chat_llm" if triggered_by == "chat_llm" else "regex_pilot"
        )
        return resume_pilot_cycle(
            state, output_root=output_root, triggered_by=resume_by
        )
    if is_generation_terminal_failure(state, output_root):
        fresh_by = (
            "chat_llm" if triggered_by == "chat_llm" else "regex_fresh"
        )
        return start_fresh_generation(
            state, output_root=output_root, triggered_by=fresh_by
        )
    if is_generation_resumable(state):
        resume_by = (
            "chat_llm" if triggered_by == "chat_llm" else "regex_resume"
        )
        return resume_generation(
            state, output_root=output_root, triggered_by=resume_by
        )
    return {
        "ok": False,
        "sse": "info",
        "summary": (
            "There is no pending blueprint to build and no interrupted "
            "coding run to resume. Describe the platform you want first."
        ),
        "stream_delta": True,
    }
