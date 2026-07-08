import json
import logging
import re
from datetime import datetime
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.session_store import get_session, update_session
from ..core.chain_generator import (
    generate_chain_suggestion,
    validate_chain,
    fetch_block_registry,
    check_chain_quality,
)
from ..core.rule_injector import inject_rules
from ..core.block_taxonomy import list_optional_blocks

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

    update_session(session_id, state)

    yield _sse_event("status", "thinking")

    try:
        suggestion = await generate_chain_suggestion(
            domain=state.config.domain,
            user_message=user_message,
            chat_history=state.chat_history[:-1],
            docs_summary=_docs_summary(state),
        )
    except Exception as exc:
        logger.exception("Chain generation failed")
        yield _sse_event("error", f"Failed to generate suggestion: {exc}")
        return

    assistant_message = suggestion.get("message", "")
    chain = suggestion.get("chain")
    rules = suggestion.get("rules", [])

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
async def chat(session_id: str, body: ChatMessage):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return StreamingResponse(
        _stream_response(session_id, body.message),
        media_type="text/event-stream",
    )


@router.get("/{session_id}/chain/preview")
async def preview_chain(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if not state.proposed_chain:
        raise HTTPException(status_code=404, detail="No chain proposed yet")
    response = {"chain": state.proposed_chain, "rules": state.extracted_rules}
    if state.chain_quality:
        response["quality"] = state.chain_quality
    return response


@router.post("/{session_id}/chain/approve")
async def approve_chain(session_id: str, body: ApproveRequest = ApproveRequest()):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
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
