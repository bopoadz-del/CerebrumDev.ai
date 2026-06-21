import json
import logging
from datetime import datetime
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.session_store import get_session, update_session
from ..core.chain_generator import generate_chain_suggestion, validate_chain, fetch_block_registry
from ..core.rule_injector import inject_rules

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


async def _stream_response(session_id: str, user_message: str) -> AsyncGenerator[str, None]:
    state = get_session(session_id)
    if not state:
        yield _sse_event("error", "Session not found")
        return

    state.chat_history.append({"role": "user", "content": user_message})
    state.updated_at = datetime.utcnow()
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
            yield _sse_event("chain", json.dumps(chain))
        else:
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
    return {"chain": state.proposed_chain, "rules": state.extracted_rules}


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
