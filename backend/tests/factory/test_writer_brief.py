"""The coding agent receives ONE gated brief — not a swarm of product stories."""

from __future__ import annotations

from app.factory.build.level_grade import Level
from app.factory.build.writer_brief import CODING_AGENT_BRIEF, writer_system_brief
from app.factory.coder import _PLATFORM_SYSTEM, _ROUTE_SYSTEM, _SPEC_SYSTEM


def test_one_brief_names_gates_pilot_ready_and_forbids_thin_success():
    brief = writer_system_brief()
    assert brief.startswith("You are the Factory coding agent")
    assert CODING_AGENT_BRIEF in brief
    for name in (
        Level.CODE_GREEN.value,
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
        "pilot_ready",
    ):
        assert name in brief
    assert "CODE" in brief and "PRODUCT" in brief and "STORE" in brief
    lowered = brief.lower()
    assert "thin" in lowered and "scaffold" in lowered
    assert "templates-only" in lowered or "stub" in lowered
    assert "action=" in brief
    assert "EVERY id in BLOCK_IDS" in brief
    assert "Finished" in brief or "finished product" in lowered


def test_handler_spec_and_route_share_the_same_brief():
    """Leftover per-cap packets still share the one system brief (FACTORY_BRIEF_DISPATCH=0)."""
    brief = writer_system_brief()
    assert brief in _PLATFORM_SYSTEM
    assert brief in _SPEC_SYSTEM
    assert brief in _ROUTE_SYSTEM
    assert _PLATFORM_SYSTEM.index(brief) == 0
    assert _SPEC_SYSTEM.index(brief) == 0
    assert _ROUTE_SYSTEM.index(brief) == 0


def test_generate_platform_handler_sends_the_one_brief(monkeypatch):
    import app.factory.coder as coder

    captured = {}

    def _capture(messages, *a, **k):
        captured["messages"] = messages
        return (
            "results = {}\n"
            "for block_id in BLOCK_IDS:\n"
            "    results[block_id] = execute(block_id, payload, "
            "action=BLOCK_DEFAULT_ACTIONS.get(block_id))\n"
            'return {"ok": True, "capability": CAPABILITY_ID, "results": results}',
            "stub-model",
        )

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr(coder, "_llm_code_call", _capture)

    coder.generate_platform_handler(
        capability_id="site_visits",
        description="log a visit",
        block_ids=["workflow"],
        product_name="VetConnect",
        vertical="veterinary_services",
    )
    system = captured["messages"][0]
    assert system["role"] == "system"
    assert writer_system_brief() in system["content"]
    assert "pilot_ready" in system["content"]
    assert "CODE_GREEN" in system["content"]
    user = captured["messages"][-1]["content"]
    assert "Write the handle() body now." in user


def test_rework_packet_keeps_the_same_system_brief(monkeypatch):
    import app.factory.coder as coder

    captured = {}

    def _capture(messages, *a, **k):
        captured["messages"] = messages
        return (
            "res = execute(BLOCK_IDS[0], payload, "
            "action=BLOCK_DEFAULT_ACTIONS.get(BLOCK_IDS[0]))\n"
            'return {"ok": True, "capability": CAPABILITY_ID, "res": res}',
            "stub-model",
        )

    monkeypatch.setattr(coder, "_llm_code_call", _capture)
    coder.generate_platform_handler(
        capability_id="site_visits",
        description="log a visit",
        block_ids=["workflow"],
        product_name="VetConnect",
        vertical="veterinary_services",
        work_list=["site_visits rejected a payload"],
        previous_attempt="return {}",
    )
    assert writer_system_brief() in captured["messages"][0]["content"]
    user = captured["messages"][-1]["content"]
    assert "site_visits rejected a payload" in user
