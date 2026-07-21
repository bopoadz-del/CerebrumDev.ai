"""Tests for the chat-driven platform creation bridge.

The bridge routes free-text chat messages onto the existing product_design
state machine (same functions as routers/session_product.py). These tests
cover intent detection and the draft -> approve -> generate flow.
"""

from __future__ import annotations

import pytest

from app.factory import platform_chat_flow
from app.models.session import SessionState


# --- Intent detection -------------------------------------------------------

@pytest.mark.parametrize("message", [
    "build me a platform for boutique hotels",
    "Create a platform for fleet management",
    "I want to make a new product for clinics",
    "design a platform for private estates",
    "generate a system for warehouse operations",
    "assemble a portal for suppliers",
])
def test_platform_intent_positive(message):
    assert platform_chat_flow.is_platform_intent(message)


@pytest.mark.parametrize("message", [
    "use domain retail",
    "what blocks are available?",
    "set the learning rate to 0.0002",
    "build the chain please",                      # chain config, not a product
    "tell me about your platform",                  # question, not an instruction
    "build a chain with the blocks for hotels",     # kit vocabulary -> legacy flow
    "create a product chain for the retail domain", # kit vocabulary -> legacy flow
    "",
])
def test_platform_intent_negative(message):
    assert not platform_chat_flow.is_platform_intent(message)


@pytest.mark.parametrize("message", [
    "approve",
    "approved",
    "go ahead",
    "looks good",
    "yes, build it",
    "generate it",
])
def test_approval_positive(message):
    assert platform_chat_flow.is_approval(message)


@pytest.mark.parametrize("message", [
    "build me a platform for hotels",
    "what do you think?",
    "",
])
def test_approval_negative(message):
    assert not platform_chat_flow.is_approval(message)


# --- Flow: draft -> pending -> approve -> generate ---------------------------

def _fresh_state() -> SessionState:
    return SessionState(session_id="test-session-chat-platform", user_id="test-user")


def test_draft_from_chat_parks_blueprint_on_session():
    state = _fresh_state()
    result = platform_chat_flow.draft_from_chat(
        state, "build me a platform for private estates"
    )
    assert result["ok"] is True
    assert state.product_design.blueprint is not None
    assert state.product_design.blueprint_approved is False
    assert state.product_design.generation is None
    assert platform_chat_flow.has_pending_blueprint(state)
    # estate-related brief -> golden steward source
    assert result["source"] == "golden_steward"


def test_approve_and_generate_produces_product(tmp_path):
    state = _fresh_state()
    platform_chat_flow.draft_from_chat(
        state, "build me a platform for private estates"
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is True
    assert state.product_design.blueprint_approved is True
    assert state.product_design.plan is not None
    assert state.product_design.generation is not None
    output_dir = tmp_path / state.product_design.generation["product_id"]
    assert output_dir.exists()
    assert any(output_dir.iterdir()), "generated product dir must not be empty"
    assert not platform_chat_flow.has_pending_blueprint(state)  # approved now


def test_approve_without_draft_raises():
    state = _fresh_state()
    with pytest.raises(ValueError, match="no blueprint"):
        platform_chat_flow.approve_and_generate(state, output_root=None)


def test_draft_resets_previous_design():
    state = _fresh_state()
    platform_chat_flow.draft_from_chat(state, "build me a platform for hotels")
    platform_chat_flow.draft_from_chat(state, "create a platform for clinics")
    # second draft replaces the first, approval flag reset
    assert state.product_design.blueprint_approved is False
    assert state.product_design.generation is None
    assert state.product_design.brief == "create a platform for clinics"
