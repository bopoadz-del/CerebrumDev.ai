"""CI copy of architect LLM drafting gates (backend/app/tests is not collected)."""

from __future__ import annotations

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


def _del_keys(monkeypatch):
    monkeypatch.delenv(product_architect.LLM_DRAFTING_ENV, raising=False)
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_key_present_flag_unset_drafts_with_llm(monkeypatch):
    monkeypatch.delenv(product_architect.LLM_DRAFTING_ENV, raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "sk-test-not-used")
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: ["audit", "chat", "vector_search"])
    monkeypatch.setattr(product_architect, "_llm_json_call", lambda messages: _LLM_PAYLOAD)
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert bp.drafting_mode == "architect_llm"
    assert bp.product_name == "FleetOps Live"


def test_explicit_off_wins_even_with_key(monkeypatch):
    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "0")
    monkeypatch.setenv("KIMI_API_KEY", "sk-test-not-used")
    calls = []
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: ["audit"])
    monkeypatch.setattr(product_architect, "_llm_json_call", lambda messages: calls.append(messages) or _LLM_PAYLOAD)
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert calls == []
    assert bp.drafting_mode == "keyword_fallback"


def test_no_key_uses_keyword_path(monkeypatch):
    _del_keys(monkeypatch)
    calls = []
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: ["audit"])
    monkeypatch.setattr(product_architect, "_llm_json_call", lambda messages: calls.append(messages) or _LLM_PAYLOAD)
    bp = product_architect.draft_blueprint_from_brief(_BRIEF)
    assert calls == []
    assert bp.vertical == "fleet_operations"
