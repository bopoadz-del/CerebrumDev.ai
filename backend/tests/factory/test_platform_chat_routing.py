"""Factory-floor routing triage — retail build must never leak to the legacy
kit-chain generator, and card summaries must be emitted exactly once.

Covers the two live bugs from user testing:
(a) "Generated chain failed validation" on a standard retail request — the
    message fell through to the legacy chain path (either the intent regex
    missed the phrasing, or a pending blueprint + unrecognized message leaked
    through). The kernel refusal was correct; the routing was not.
(b) Doubled blueprint text — the summary was emitted both in the card event
    and again as a word-by-word delta stream appended to the same bubble.
"""

from __future__ import annotations

import json

import pytest

from app.factory import platform_chat_flow
from app.models.session import ProductDesignState, SessionState


# --- (a) intent coverage: standard platform phrasings enter the platform flow


@pytest.mark.parametrize(
    "message",
    [
        "build a platform for retail",
        "build me a retail platform",
        "create a platform for my retail business",
        "I need a platform for retail",
        "i want a retail system",
        "give me a retail platform",
        "we are looking for a portal for retail operations",
        "I'd like a product for retail inventory",
        "set up a platform for retail",
    ],
)
def test_standard_platform_phrasings_route_to_platform_flow(message):
    assert platform_chat_flow.should_handle_platform_message(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "add block vector_search to the chain",
        "what learning rate does the lora use",
        "show me the retail kit blocks",
    ],
)
def test_kit_configurator_vocabulary_stays_legacy(message):
    assert platform_chat_flow.should_handle_platform_message(message) is False


# --- (b) + fallthrough: SSE stream contract ---------------------------------


def _state_with_pending_blueprint() -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    s = SessionState(session_id="sess-routing", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    bp = draft_blueprint_from_brief("build a platform for retail")
    s.product_design.blueprint = bp.model_dump(mode="json")
    return s


async def _collect_events(session_id: str, message: str):
    from app.routers import chat as chat_router

    events = []
    async for raw in chat_router._stream_response(session_id, message):
        lines = [l for l in raw.strip().splitlines() if l]
        ev = {"event": "", "data": ""}
        for line in lines:
            if line.startswith("event:"):
                ev["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line.split(":", 1)[1].strip())
        events.append(ev)
    return events


@pytest.fixture
def session(monkeypatch):
    from app.core import session_store

    state = _state_with_pending_blueprint()
    session_store._session_store[state.session_id] = state
    yield state
    session_store._session_store.pop(state.session_id, None)


@pytest.mark.asyncio
async def test_pending_blueprint_unrecognized_message_never_hits_legacy_chain(session):
    # An off-script message while a blueprint is pending must produce
    # platform-flow guidance — no legacy chain generation, no
    # "Generated chain failed validation", no LLM call at all.
    events = await _collect_events(session.session_id, "make the theme dark please")
    kinds = [e["event"] for e in events]
    assert "error" not in kinds
    assert "chain" not in kinds
    assert "status" not in kinds  # legacy path emits status:thinking first
    text = " ".join(e["data"] for e in events if e["event"] == "delta")
    assert "approve" in text.lower()
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_blueprint_summary_emitted_exactly_once(session):
    # Repeated build message re-drafts: the summary must arrive ONLY in the
    # blueprint card event — zero delta re-stream (that doubled the text).
    events = await _collect_events(session.session_id, "build a platform for retail")
    kinds = [e["event"] for e in events]
    assert kinds.count("blueprint") == 1
    assert "delta" not in kinds
    bp = next(e["data"] for e in events if e["event"] == "blueprint")
    assert json.loads(bp)["summary"]


@pytest.mark.asyncio
async def test_approval_generation_emitted_exactly_once(session, monkeypatch):
    def fake_approve(state, **kwargs):
        return {"ok": True, "summary": "Platform generated.", "product_id": "p1"}

    monkeypatch.setattr(platform_chat_flow, "approve_and_generate", fake_approve)
    events = await _collect_events(session.session_id, "approve")
    kinds = [e["event"] for e in events]
    assert kinds.count("generation") == 1
    assert "delta" not in kinds
    assert kinds[-1] == "done"
