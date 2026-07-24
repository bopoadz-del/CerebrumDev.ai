"""Platform chat flow refinement commands and deterministic vertical extraction."""

from __future__ import annotations

import pytest

from app.factory import platform_chat_flow
from app.factory.platform_chat_flow import refine_from_chat
from app.factory.product_architect import _vertical_from_brief
from app.models.session import ProductDesignState, SessionState


@pytest.fixture
def state():
    s = SessionState(session_id="sess-refine", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    return s


def _draft_retail(state):
    from app.factory.product_architect import draft_blueprint_from_brief

    bp = draft_blueprint_from_brief(
        "build me secure multi users platform for my retail business"
    )
    state.product_design.blueprint = bp.model_dump(mode="json")
    return bp


def test_refinement_add_capability(state):
    _draft_retail(state)
    result = refine_from_chat(state, "add capability vector_search")
    assert result["ok"] is True
    assert result["refined"] is True
    cap_ids = [c["id"] for c in result["blueprint"]["capabilities"]]
    assert "vector_search" in cap_ids
    vs = next(c for c in result["blueprint"]["capabilities"] if c["id"] == "vector_search")
    assert vs["strategy_hint"] == "REUSE"
    assert vs["block_ids"] == ["vector_search"]


def test_refinement_add_unknown_capability_becomes_generate(state):
    _draft_retail(state)
    result = refine_from_chat(state, "add capability loyalty_rewards")
    assert result["ok"] is True
    cap = next(c for c in result["blueprint"]["capabilities"] if c["id"] == "loyalty_rewards")
    assert cap["strategy_hint"] == "GENERATE"
    assert cap["block_ids"] == []


def test_refinement_remove_capability(state):
    _draft_retail(state)
    before = [c["id"] for c in state.product_design.blueprint["capabilities"]]
    assert "audit" in before
    result = refine_from_chat(state, "remove capability audit")
    assert result["ok"] is True
    after = [c["id"] for c in result["blueprint"]["capabilities"]]
    assert "audit" not in after


def test_refinement_cannot_remove_last_capability(state):
    _draft_retail(state)
    # remove audit first
    refine_from_chat(state, "remove capability audit")
    # now only retail_core remains
    result = refine_from_chat(state, "remove capability retail_core")
    assert result["ok"] is False


def test_refinement_list_capabilities(state):
    _draft_retail(state)
    result = refine_from_chat(state, "list capabilities")
    assert result["ok"] is True
    assert "retail_core" in result["summary"]


def test_refinement_rename_product(state):
    _draft_retail(state)
    result = refine_from_chat(state, 'rename product to "Acme Retail Hub"')
    assert result["ok"] is True
    assert result["blueprint"]["product_name"] == "Acme Retail Hub"


def test_refinement_set_vertical(state):
    _draft_retail(state)
    result = refine_from_chat(state, "change vertical to boutique_retail")
    assert result["ok"] is True
    assert result["blueprint"]["vertical"] == "boutique_retail"
    assert result["blueprint"]["product_id"] == "boutique_retail"


def test_vertical_extracts_domain_after_platform_for():
    """The user's original brief must resolve to retail, not users."""
    assert _vertical_from_brief(
        "build me secure multi users platform for my retail business"
    ) == "retail"


def test_vertical_prefers_descriptor_before_platform():
    assert _vertical_from_brief(
        "create an inventory management platform for retail stores"
    ) == "inventory_management"


def test_vertical_normalizes_multi_user():
    assert _vertical_from_brief("build a multi users retail platform") == "retail"


def test_parse_refinement_command_variations():
    assert platform_chat_flow.parse_refinement_command("add capability payments")[0] == "add_capability"
    assert platform_chat_flow.parse_refinement_command("include chat")[0] == "add_capability"
    assert platform_chat_flow.parse_refinement_command("drop capability audit")[0] == "remove_capability"
    assert platform_chat_flow.parse_refinement_command("list capabilities")[0] == "list_capabilities"
