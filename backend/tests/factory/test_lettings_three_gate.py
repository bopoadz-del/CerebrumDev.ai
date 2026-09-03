"""Residential-lettings three-gate re-walk + Level grade.

A lettings brief must draft the golden roster (not a GENERATE stub), the
code cycle must emit a full 14-class repo, and a Store-green pilot is the
only path to ``pilot_ready`` / founding-customer-ready. Fail-closed if the
pilot cycle is red.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES
from app.factory.build.level_grade import Level
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build_jobs import build_status
from app.factory.product_architect import (
    draft_blueprint_from_brief,
    lettings_golden_path,
)


ROOT = Path(__file__).resolve().parents[3]
LETTINGS = ROOT / "blueprints" / "lettings" / "residential_lettings.v1.yaml"
LIVE_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "KIMI_MOCK",
        "CEREBRUM_LLM_MOCK",
    ):
        monkeypatch.delenv(var, raising=False)


def test_lettings_brief_drafts_the_golden_roster_not_a_keyword_stub():
    """Keyword fallback of this brief was a GENERATE residential_lettings_core."""
    bp = draft_blueprint_from_brief(
        "build a platform for residential lettings",
        use_llm=False,
    )
    assert bp.drafting_mode == "golden_lettings"
    assert bp.product_id == "residential-lettings"
    assert bp.vertical == "residential_lettings"
    assert {c.id for c in bp.capabilities} == LIVE_CAPS
    assert not any(c.strategy_hint == "GENERATE" for c in bp.capabilities)
    assert all(c.block_ids for c in bp.capabilities)


def test_manchester_lettings_brief_also_uses_the_golden():
    bp = draft_blueprint_from_brief(
        "build me a platform for residential lettings in Manchester",
        use_llm=False,
    )
    assert bp.drafting_mode == "golden_lettings"
    assert bp.product_id == "residential-lettings"


# Live d4f6da2: Floor "New session" with this branded brief built
# property-management (6 capabilities) — keyword/LLM GENERATE — instead of
# the golden roster. The #294 matcher only accepted "residential lettings"
# / "lettings platform", and LLM-first routing stole even those.
NORTHBRIDGE_BRIEF = (
    "Northbridge Lettings Desk — UK residential lettings CRM for landlords "
    "and tenants in Leeds: property portfolio, tenancy applications, viewing "
    "bookings, rent collection tracking, maintenance tickets, document vault. "
    "Brand: Northbridge Lettings."
)


def _assert_golden_lettings_roster(bp) -> None:
    assert bp.drafting_mode == "golden_lettings"
    assert bp.product_id == "residential-lettings"
    assert bp.vertical == "residential_lettings"
    assert {c.id for c in bp.capabilities} == LIVE_CAPS
    assert not any(c.strategy_hint == "GENERATE" for c in bp.capabilities)
    assert all(c.block_ids for c in bp.capabilities)


def test_northbridge_branded_brief_drafts_the_golden_roster():
    bp = draft_blueprint_from_brief(NORTHBRIDGE_BRIEF, use_llm=False)
    _assert_golden_lettings_roster(bp)


@pytest.mark.parametrize(
    "brief",
    [
        "Northbridge Lettings Desk — lettings CRM for landlords and tenants in Leeds",
        "lettings CRM for landlords and tenants in Leeds",
        "UK tenancy applications and viewing bookings for a lettings desk",
        "Brand: Northbridge Lettings. Property portfolio and rent collection.",
    ],
)
def test_branded_and_longer_lettings_briefs_use_the_golden(brief):
    bp = draft_blueprint_from_brief(brief, use_llm=False)
    _assert_golden_lettings_roster(bp)


def test_generic_property_management_is_not_the_lettings_golden():
    """A non-lettings property-management brief stays on keyword GENERATE."""
    bp = draft_blueprint_from_brief(
        "build a property management platform",
        use_llm=False,
    )
    assert bp.drafting_mode == "keyword_fallback"
    assert bp.vertical == "property_management"
    assert bp.product_id == "property-management"
    assert any(c.id.endswith("_core") and c.strategy_hint == "GENERATE" for c in bp.capabilities)
    assert {c.id for c in bp.capabilities} != LIVE_CAPS


def test_floor_chat_draft_labels_northbridge_as_golden():
    from app.factory.blueprint import ProductBlueprint
    from app.factory.platform_chat_flow import draft_from_chat
    from app.models.session import ProductDesignState, SessionState

    state = SessionState(session_id="sess-northbridge", user_id="u1", account_id="a1")
    state.product_design = ProductDesignState()
    result = draft_from_chat(state, NORTHBRIDGE_BRIEF)
    assert result["drafting_mode"] == "golden_lettings"
    assert result["source"] == "golden_lettings"
    assert "golden residential-lettings" in result["summary"].lower()
    _assert_golden_lettings_roster(ProductBlueprint.model_validate(result["blueprint"]))


def test_lettings_golden_yaml_is_the_same_file_the_architect_loads():
    assert lettings_golden_path() == LETTINGS
    loaded = load_blueprint(LETTINGS)
    assert loaded.product_id == "residential-lettings"
    assert {c.id for c in loaded.capabilities} == LIVE_CAPS


def test_opt_out_still_allows_keyword_fallback_for_tests():
    bp = draft_blueprint_from_brief(
        "build a platform for residential lettings",
        use_llm=False,
        use_golden_lettings=False,
    )
    assert bp.drafting_mode == "keyword_fallback"
    assert any(c.id.endswith("_core") for c in bp.capabilities)


def _assert_full_repo(out: Path) -> None:
    for rel in FOURTEEN_ARTIFACT_CLASSES:
        assert (out / rel).exists(), f"{rel} missing from lettings artifact"
    for rel in (
        "Dockerfile",
        "README.md",
        "app/main.py",
        "app/block_inputs.py",
        "app/actions/viewing_management.py",
        "frontend/src/App.tsx",
        "tests/test_routes.py",
        "scripts/release_gate.py",
    ):
        assert (out / rel).is_file(), f"{rel} missing"
    routes = (out / "tests" / "test_routes.py").read_text(encoding="utf-8")
    assert "def test_every_capability_route_accepts_payload" in routes
    handler = (out / "app" / "actions" / "viewing_management.py").read_text(
        encoding="utf-8"
    )
    assert "httpx" not in handler
    assert "/v1/execute" not in handler
    assert "prepare_block_input" in handler


def test_lettings_code_cycle_is_a_full_repo_and_not_pilot_ready(tmp_path):
    out = tmp_path / "residential-lettings"
    runner = RoleRunner(
        load_blueprint(LETTINGS),
        out,
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=300),
        auto_pilot=False,
    )
    outcome = runner.run()
    assert outcome.ok, outcome.to_dict()
    assert "CODE PASS" in outcome.detail
    assert "PRODUCT NOT RUN" in outcome.detail
    assert "STORE NOT RUN" in outcome.detail
    assert runner.ledger.pilot_ready() is False
    _assert_full_repo(out)

    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is False
    assert status["cycle"] == "code"
    grade = status["level_grade"]
    assert grade["level"] == Level.CODE_GREEN.value
    assert grade["founding_customer_ready"] is False
    assert grade["three_gate"]["PRODUCT"] == "NOT_RUN"
    assert grade["three_gate"]["STORE"] == "NOT_RUN"

    env = os.environ.copy()
    env["STORAGE_PATH"] = str(out / "data")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_routes.py::test_every_capability_route_accepts_payload",
            "-q",
            "--tb=line",
        ],
        cwd=out,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_lettings_three_gate_pilot_walk_is_honest(tmp_path):
    """Code cycle then Store-green pilot. Founding only if all three gates pass.

    Vendor-mirror durability may still fail PRODUCT/STORE. That must stay a
    red Level, never a thin SUCCESS with implied Finished.
    """
    out = tmp_path / "residential-lettings"
    code = RoleRunner(
        load_blueprint(LETTINGS),
        out,
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=300),
        auto_pilot=False,
    ).run()
    assert code.ok, code.to_dict()

    pilot = RoleRunner(
        load_blueprint(LETTINGS),
        out,
        cycle="pilot",
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=300),
        auto_pilot=False,
    ).run()
    status = build_status(out)
    grade = status["level_grade"]
    _assert_full_repo(out)

    assert pilot.ok, (pilot.to_dict(), grade)
    assert runner_pilot_ready(out) is True
    assert "PRODUCT PASS" in (pilot.detail or "")
    assert "STORE PASS" in (pilot.detail or "")
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is True
    assert grade["three_gate"] == {"CODE": "PASS", "PRODUCT": "PASS", "STORE": "PASS"}
    assert grade["missing"] == []
    assert grade["blockers"] == []
    assert grade["level"] == Level.FOUNDING_CUSTOMER_READY.value
    assert grade["founding_customer_ready"] is True


def runner_pilot_ready(out: Path) -> bool:
    from app.factory.build.ledger import BuildLedger

    return BuildLedger(out / "build_ledger.jsonl").pilot_ready()
