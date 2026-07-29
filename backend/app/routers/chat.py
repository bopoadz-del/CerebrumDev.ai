import json
import logging
import re
from datetime import datetime
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.session_guard import require_owned_session
from ..core.session_store import get_session, update_session
from ..core.chain_generator import (
    generate_chain_suggestion,
    validate_chain,
    fetch_block_registry,
    check_chain_quality,
)
from ..core.grounding import evaluate_grounding, persist_verdict
from ..core.rule_injector import inject_rules
from ..core.trial_limits import TrialLimitExceeded, require_within_limit
from ..core.block_taxonomy import list_optional_blocks
from ..factory import platform_chat_flow
from ..models.session import SessionState

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatMessage(BaseModel):
    message: str


class ApproveRequest(BaseModel):
    approve: bool = True


def _docs_summary(state) -> str:
    if not state.chunks:
        return ""
    preview = " ".join(state.chunks)[:1500]
    return f"Document preview: {preview}"


def _session_state_summary(state) -> str:
    """Authoritative session facts used to ground the conversational fallback.

    The LLM fallback answers strictly from this summary; it performs no
    platform actions itself, so it must not invent them either.
    """
    pd = getattr(state, "product_design", None)
    if not pd or not getattr(pd, "blueprint", None):
        return "No platform blueprint has been drafted in this session."
    bp = pd.blueprint or {}
    caps = bp.get("capabilities") or []
    lines = [
        f"Drafted blueprint: {bp.get('product_name')} (vertical: {bp.get('vertical')}), "
        f"{len(caps)} capabilities.",
        f"Blueprint approved: {'yes' if pd.blueprint_approved else 'no'}.",
    ]
    gen = getattr(pd, "generation", None)
    if gen:
        lines.append(
            f"Generated product: {gen.get('product_id')} — available under Your Platforms."
        )
    else:
        lines.append("No product has been generated yet.")
    if getattr(pd, "last_error", None):
        lines.append(f"Last error: {pd.last_error}")
    return "\n".join(lines)


def _parse_command(message: str):
    """Detect natural-language config commands. Returns (command, args) or (None, None)."""
    lowered = message.lower().strip()

    domain_match = re.search(
        r"(?:set\s+(?:the\s+)?domain\s+(?:to\s+)?|use\s+domain\s+|domain\s+(?:is\s+)?)([a-z0-9_-]+)",
        lowered,
    )
    if domain_match:
        return "set_domain", {"domain": domain_match.group(1)}

    model_match = re.search(
        r"(?:set\s+(?:the\s+)?(?:base\s+)?model\s+(?:to\s+)?|use\s+(?:model\s+)?)([a-zA-Z0-9_.-]+\-[0-9]+[a-zA-Z0-9_-]*)",
        message,
    )
    if model_match:
        return "set_model", {"model": model_match.group(1)}

    lora_match = re.search(
        r"(?:set\s+(?:the\s+)?lora\s+rank\s+(?:to\s+)?|lora\s+rank\s+(?:to\s+)?)(\d+)",
        lowered,
    )
    if lora_match:
        return "set_lora_rank", {"lora_rank": int(lora_match.group(1))}

    lr_match = re.search(
        r"(?:set\s+(?:the\s+)?learning\s+rate\s+(?:to\s+)?|learning\s+rate\s+(?:to\s+)?)([0-9.e-]+)",
        lowered,
    )
    if lr_match:
        try:
            return "set_learning_rate", {"learning_rate": float(lr_match.group(1))}
        except ValueError:
            pass

    vdb_match = re.search(
        r"(?:set\s+(?:the\s+)?vector\s+db\s+(?:to\s+)?|use\s+vector\s+db\s+|vector\s+db\s+(?:is\s+)?)([a-z0-9]+)",
        lowered,
    )
    if vdb_match:
        return "set_vector_db", {"vector_db": vdb_match.group(1).capitalize()}

    hnsw_match = re.search(
        r"(?:set\s+(?:the\s+)?hnsw\s+(?:preset\s+)?(?:to\s+)?|hnsw\s+(?:preset\s+)?(?:to\s+)?)(fast|balanced|accurate)",
        lowered,
    )
    if hnsw_match:
        return "set_hnsw_preset", {"hnsw_preset": hnsw_match.group(1)}

    if re.search(r"^(what\s+blocks|list\s+blocks|available\s+blocks|show\s+blocks)", lowered):
        return "list_blocks", {}

    return None, None


def _apply_command(state, command: str, args: dict):
    if command == "set_domain":
        state.config.domain = args["domain"]
    elif command == "set_model":
        state.config.ai_config.base_model = args["model"]
    elif command == "set_lora_rank":
        state.config.ai_config.lora_rank = args["lora_rank"]
    elif command == "set_learning_rate":
        state.config.ai_config.learning_rate = args["learning_rate"]
    elif command == "set_vector_db":
        state.config.ai_config.vector_db = args["vector_db"]
    elif command == "set_hnsw_preset":
        state.config.ai_config.hnsw_preset = args["hnsw_preset"]


async def _stream_response(session_id: str, user_message: str) -> AsyncGenerator[str, None]:
    state = get_session(session_id)
    if not state:
        yield _sse_event("error", "Session not found")
        return

    # Server-side trial boundary: chat messages are metered per account.
    try:
        require_within_limit(getattr(state, "user_id", None), "chat_message")
    except TrialLimitExceeded as exc:
        yield _sse_event("error", exc.detail["message"])
        yield _sse_event("done", "")
        return

    state.chat_history.append({"role": "user", "content": user_message})
    state.updated_at = datetime.utcnow()

    command, args = _parse_command(user_message)
    if command:
        if command == "list_blocks":
            optional = list_optional_blocks()
            confirm = (
                "Your instance already includes the domain kit plus all built-in blocks "
                "(orchestrator, vector search, OCR, PDF, image, chat, auth, etc.).\n\n"
                "Optional primitives you can add:\n- " + "\n- ".join(optional)
            )
            state.chat_history.append({"role": "assistant", "content": confirm})
            update_session(session_id, state)
            for word in confirm.split(" "):
                yield _sse_event("delta", word + " ")
            yield _sse_event("done", "")
            return

        _apply_command(state, command, args)
        update_session(session_id, state)
        yield _sse_event("command", json.dumps({"command": command, "args": args}))
        confirm = f"Updated: {command.replace('_', ' ')}."
        state.chat_history.append({"role": "assistant", "content": confirm})
        update_session(session_id, state)
        for word in confirm.split(" "):
            yield _sse_event("delta", word + " ")
        yield _sse_event("done", "")
        return

    # --- Platform-creation flow: chat bridges to the product state machine ---
    # Routing contract lives in platform_chat_flow.should_handle_platform_message:
    # explicit commands always enter; free-text intent only when the env gate
    # is on; approvals only when a blueprint is actually pending.
    try:
        # NOTE on emission: card events (blueprint/generation) already carry
        # the full summary and the frontend renders it from the card. Do NOT
        # also word-stream the same summary as `delta` tokens — the UI appends
        # deltas to the card bubble, which doubled every blueprint text
        # ("Blueprint drafted... Blueprint drafted..."). `info` has no card
        # renderer, so info replies stream as deltas only.
        if platform_chat_flow.has_pending_blueprint(state) and platform_chat_flow.is_approval(user_message):
            # Server-side trial boundary: generations are metered per account.
            try:
                require_within_limit(getattr(state, "user_id", None), "generation")
            except TrialLimitExceeded as exc:
                yield _sse_event("error", exc.detail["message"])
                yield _sse_event("done", "")
                return
            result = platform_chat_flow.approve_and_generate(state)
            state.chat_history.append({"role": "assistant", "content": result["summary"]})
            state.updated_at = datetime.utcnow()
            update_session(session_id, state)
            yield _sse_event("generation", json.dumps(result))
            yield _sse_event("done", "")
            return

        if platform_chat_flow.has_pending_blueprint(state):
            refined = platform_chat_flow.refine_from_chat(state, user_message)
            if refined:
                state.chat_history.append({"role": "assistant", "content": refined["summary"]})
                state.updated_at = datetime.utcnow()
                update_session(session_id, state)
                if refined.get("refined"):
                    yield _sse_event("blueprint", json.dumps(refined))
                else:
                    yield _sse_event("info", json.dumps(refined))
                    for word in refined["summary"].split(" "):
                        yield _sse_event("delta", word + " ")
                yield _sse_event("done", "")
                return
            if not platform_chat_flow.should_handle_platform_message(user_message):
                # A blueprint is pending and the message is neither an
                # approval, a refinement command, nor a new platform brief.
                # Stay IN the platform flow — falling through to the legacy
                # kit-chain generator here made a pending retail blueprint
                # end in "Generated chain failed validation" (the LLM invents
                # block ids the registry rejects; the refusal is correct, the
                # routing was not).
                guidance = (
                    "A blueprint is drafted and waiting. Say 'approve' to "
                    "generate it, refine it ('add capability X', 'remove "
                    "capability X', 'rename product to Y'), or describe a "
                    "different platform to start over."
                )
                state.chat_history.append({"role": "assistant", "content": guidance})
                state.updated_at = datetime.utcnow()
                update_session(session_id, state)
                for word in guidance.split(" "):
                    yield _sse_event("delta", word + " ")
                yield _sse_event("done", "")
                return

        if platform_chat_flow.should_handle_platform_message(user_message):
            result = platform_chat_flow.draft_from_chat(state, user_message)
            state.chat_history.append({"role": "assistant", "content": result["summary"]})
            state.updated_at = datetime.utcnow()
            update_session(session_id, state)
            yield _sse_event("blueprint", json.dumps(result))
            yield _sse_event("done", "")
            return
    except Exception as exc:  # honest failure, stay in chat
        logger.exception("Platform flow failed")
        message = (
            "Platform flow hit a blocker: "
            f"{exc}. Nothing was generated; refine the brief or check the factory logs."
        )
        state.chat_history.append({"role": "assistant", "content": message})
        state.updated_at = datetime.utcnow()
        update_session(session_id, state)
        yield _sse_event("error", message)
        yield _sse_event("done", "")
        return

    update_session(session_id, state)

    yield _sse_event("status", "thinking")

    try:
        suggestion = await generate_chain_suggestion(
            domain=state.config.domain,
            user_message=user_message,
            chat_history=state.chat_history[:-1],
            docs_summary=_docs_summary(state),
            session_state=_session_state_summary(state),
        )
    except Exception as exc:
        logger.exception("Chain generation failed")
        yield _sse_event("error", f"Failed to generate suggestion: {exc}")
        return

    assistant_message = suggestion.get("message", "")
    chain = suggestion.get("chain")
    rules = suggestion.get("rules", [])

    # Mandatory grounding stage: the LLM answer never reaches the stream
    # unverified. Blocked → answer null; flagged → estimate disclosure attached.
    grounding_sources = [
        _docs_summary(state),
        _session_state_summary(state),
        *[m.get("content", "") for m in state.chat_history],
    ]
    verdict = evaluate_grounding(
        assistant_message, sources=grounding_sources, query=user_message
    )
    persist_verdict(
        {
            "surface": "chat",
            "session_id": session_id,
            "query": user_message,
            "verdict": verdict["verdict"],
            "reasons": verdict["reasons"],
            "unsupported_figures": verdict["unsupported_figures"],
        }
    )
    yield _sse_event(
        "grounding",
        json.dumps(
            {"verdict": verdict["verdict"], "answer": verdict["allowed_response"]}
        ),
    )
    if verdict["verdict"] == "blocked":
        # The refusal must not echo the invented content; full reasons live
        # only in the persisted verdict record.
        refusal = (
            "I can't answer that from the grounded session context — the drafted "
            "reply contained unverifiable claims (such as links or figures with "
            "no grounded source) and was withheld."
        )
        state.chat_history.append({"role": "assistant", "content": refusal})
        state.updated_at = datetime.utcnow()
        update_session(session_id, state)
        for word in refusal.split(" "):
            yield _sse_event("delta", word + " ")
        yield _sse_event("done", "")
        return

    assistant_message = verdict["allowed_response"]

    # Stream the assistant message word-by-word for UI effect
    words = assistant_message.split(" ")
    for word in words:
        yield _sse_event("delta", word + " ")

    state.chat_history.append({"role": "assistant", "content": assistant_message})

    if chain:
        registry = await fetch_block_registry()
        if validate_chain(chain, list(registry.keys())):
            state.proposed_chain = chain
            state.validation_passed = True
            state.chain_quality = check_chain_quality(state.config.domain, chain, True)
            chain_payload = {"chain": chain}
            if state.chain_quality:
                chain_payload["quality"] = state.chain_quality
            yield _sse_event("chain", json.dumps(chain_payload))
        else:
            state.validation_passed = False
            state.chain_quality = None
            yield _sse_event("error", "Generated chain failed validation")

    if rules:
        state.extracted_rules = list(set(state.extracted_rules + rules))
        yield _sse_event("rules", json.dumps(rules))

    state.updated_at = datetime.utcnow()
    update_session(session_id, state)

    yield _sse_event("done", "")


def _sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/{session_id}/chat")
async def chat(
    body: ChatMessage,
    state: SessionState = Depends(require_owned_session),
):
    return StreamingResponse(
        _stream_response(state.session_id, body.message),
        media_type="text/event-stream",
    )


@router.get("/{session_id}/chain/preview")
async def preview_chain(state: SessionState = Depends(require_owned_session)):
    if not state.proposed_chain:
        raise HTTPException(status_code=404, detail="No chain proposed yet")
    response = {"chain": state.proposed_chain, "rules": state.extracted_rules}
    if state.chain_quality:
        response["quality"] = state.chain_quality
    return response


@router.post("/{session_id}/chain/approve")
async def approve_chain(
    body: ApproveRequest = ApproveRequest(),
    state: SessionState = Depends(require_owned_session),
):
    session_id = state.session_id
    if not state.proposed_chain:
        raise HTTPException(status_code=400, detail="No chain to approve")

    state.chain_approved = body.approve
    if body.approve:
        state.phase = 4
        state.phase_status = "in_progress"
        if state.extracted_rules:
            try:
                state.container_modified_path = inject_rules(
                    session_id, state.config.domain, state.extracted_rules
                )
                state.rules_injected = True
            except Exception as exc:
                logger.exception("Rule injection failed")
                raise HTTPException(status_code=500, detail=f"Rule injection failed: {exc}")

    state.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return {
        "chain_approved": state.chain_approved,
        "rules_injected": state.rules_injected,
        "container_modified_path": state.container_modified_path,
        "phase": state.phase,
    }
