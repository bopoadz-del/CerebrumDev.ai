"""Tests for LLM-powered brief drafting in the product architect.

Contract:
- gated by ARCHITECT_LLM_DRAFTING_ENABLED (default OFF — house flag pattern)
- golden steward always wins for estate briefs, even with the gate on
- fail-SAFE: LLM errors / malformed payloads / empty capabilities fall back
  to deterministic keyword drafting — a draft is always produced
- fail-closed on blocks: LLM block ids are filtered against the dual
  registry; invented ids never reach the blueprint
"""

from __future__ import annotations

import pytest

from app.factory import product_architect


_BRIEF = "fleet operations platform for a logistics company with live tracking"

_LLM_PAYLOAD = {
    "product_name": "FleetOps Live",
    "vertical": "fleet_management",
    "summary": "Live fleet operations with tracking and alerts.",
    "capabilities": [
        {
            "id": "live_tracking",
            "description": "Real-time vehicle tracking",
            "block_ids": ["vector_search", "invented_block_xyz"],
            "strategy_hint": "REUSE",
        },
        {
            "id": "driver_chat",
            "description": "Driver communication channel",
            "block_ids": ["chat"],
            "strategy_hint": "REUSE",
        },
        {
            "id": "route_optimizer",
            "description": "Novel route optimisation engine",
            "block_ids": [],
            "strategy_hint": "GENERATE",
        },
    ],
}


@pytest.fixture
def fake_dual_registry(monkeypatch):
    ids = ["audit", "chat", "vector_search", "ocr"]
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: ids)
    return ids


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the network call with a controllable fake. Returns a recorder."""
    calls = []

    def _fake(messages):
        calls.append(messages)
        return _LLM_PAYLOAD

    monkeypatch.setattr(product_architect, "_llm_json_call", _fake)
    return calls


def test_gate_off_by_default_uses_keyword_path(monkeypatch, fake_dual_registry, fake_llm):
    monkeypatch.delenv(product_architect.LLM_DRAFTING_ENV, raising=False)
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert fake_llm == [], "LLM must not be called when the gate is off"
    # keyword path: generic vertical, capabilities from mentioned blocks only
    assert bp.vertical == "product"


def test_gate_on_drafts_with_llm(monkeypatch, fake_dual_registry, fake_llm):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert len(fake_llm) == 1, "LLM must be called exactly once"
    assert bp.product_name == "FleetOps Live"
    assert bp.vertical == "fleet_management"
    cap_ids = [c.id for c in bp.capabilities]
    assert cap_ids == ["live_tracking", "driver_chat", "route_optimizer"]


def test_llm_block_ids_filtered_against_dual_registry(
    monkeypatch, fake_dual_registry, fake_llm
):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    all_blocks = [b for c in bp.capabilities for b in c.block_ids]
    assert "invented_block_xyz" not in all_blocks, "invented ids must be dropped"
    assert set(all_blocks) <= set(fake_dual_registry)
    # strategy follows the filtered result
    caps = {c.id: c for c in bp.capabilities}
    assert caps["route_optimizer"].strategy_hint == "GENERATE"
    assert caps["driver_chat"].strategy_hint == "REUSE"


def test_llm_error_falls_back_to_keyword(monkeypatch, fake_dual_registry):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")

    def _boom(messages):
        raise RuntimeError("provider down")

    monkeypatch.setattr(product_architect, "_llm_json_call", _boom)
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert bp is not None  # a draft is always produced
    assert bp.vertical == "product"  # keyword path marker


def test_llm_garbage_payload_falls_back(monkeypatch, fake_dual_registry):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")
    monkeypatch.setattr(
        product_architect, "_llm_json_call", lambda messages: {"nope": True}
    )
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert bp.vertical == "product"


def test_llm_empty_capabilities_falls_back(monkeypatch, fake_dual_registry):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")
    monkeypatch.setattr(
        product_architect,
        "_llm_json_call",
        lambda messages: {"product_name": "X", "capabilities": []},
    )
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert bp.vertical == "product"


def test_golden_steward_still_wins_with_gate_on(
    monkeypatch, fake_dual_registry, fake_llm
):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")
    bp = product_architect.draft_blueprint_from_brief(
        "platform for private estate operations with staff and vehicles"
    )
    assert fake_llm == [], "golden steward short-circuits before the LLM"
    assert bp.product_id == "cerebrum-steward"


def test_use_llm_explicit_override(monkeypatch, fake_dual_registry, fake_llm):
    monkeypatch.delenv(product_architect.LLM_DRAFTING_ENV, raising=False)
    bp = product_architect.draft_blueprint_from_brief(_BRIEF, use_llm=True)
    assert len(fake_llm) == 1
    assert bp.product_name == "FleetOps Live"
