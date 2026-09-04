"""One gated brief replaces per-capability WRITER shots."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES
from app.factory.build.brief_compiler import (
    TEMPLATE_REVISION,
    InventoryHalt,
    compile_brief,
    compile_inventory,
    load_brief_template,
    verify_inventory,
)
from app.factory.build.brief_lint import lint_brief
from app.factory.product_architect import (
    draft_blueprint_from_brief,
    lettings_golden_path,
    plan_blueprint,
)


ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"
LIVE_LETTINGS_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


class _Cap:
    def __init__(self, cid, block_ids=(), strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def test_compiled_brief_has_the_gated_shape():
    bp = load_blueprint(SMOKE)
    plan = plan_blueprint(bp)
    compiled = compile_brief(bp, plan)
    text = compiled.text
    for heading in (
        "TARGET",
        "STEP 0 INVENTORY + STOP",
        "DO",
        "ACCEPTANCE",
        "FORBIDDEN",
    ):
        assert heading in text
    assert "analytics_surface" in text
    assert "dashboard_surface" in text
    assert "one handle()" in text.lower() or "not one handle()" in text.lower()
    assert "thin SUCCESS" in text
    for vocab in ENVELOPE_STATUS_VALUES:
        assert vocab in text
    assert compiled.missing_reuse == []
    verify_inventory(compiled)
    assert "CUT 1" in text
    assert "CUT 2" in text
    assert "CUT 3" in text
    assert "Budget wall:" in text
    assert TEMPLATE_REVISION in text
    assert "READS" in text and "WRITES" in text and "NEVER" in text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert "llm_writes_brief: never" in load_brief_template()


def test_claimed_reuse_missing_from_store_halts():
    plan = _Plan(_Cap("appointments", ["not_a_real_block"], "REUSE"))
    compiled = compile_brief(_Blueprint(), plan, store_ids={"analytics", "dashboard"})
    assert compiled.missing_reuse == ["not_a_real_block"]
    assert compiled.inventory[0].missing == ["not_a_real_block"]
    with pytest.raises(InventoryHalt, match="not_a_real_block"):
        verify_inventory(compiled)


def test_verified_reuse_is_not_a_gap():
    items = compile_inventory(
        _Plan(_Cap("appointments", ["event_bus"], "REUSE")),
        {"event_bus", "database"},
    )
    assert items[0].verified_present == ["event_bus"]
    assert items[0].missing == []
    assert items[0].is_reuse


def test_capability_without_blocks_is_a_named_gap():
    items = compile_inventory(_Plan(_Cap("custom_intake", [], "GENERATE")), {"analytics"})
    assert items[0].is_gap
    assert items[0].missing == []


def test_lettings_golden_is_unchanged_and_compiles():
    """The golden roster is the live capability set — compiler must not rewrite it."""
    bp = draft_blueprint_from_brief(
        "build a platform for residential lettings",
        use_llm=False,
    )
    assert bp.drafting_mode == "golden_lettings"
    assert {c.id for c in bp.capabilities} == LIVE_LETTINGS_CAPS
    golden = load_blueprint(lettings_golden_path())
    assert {c.id for c in golden.capabilities} == LIVE_LETTINGS_CAPS
    plan = plan_blueprint(bp)
    compiled = compile_brief(bp, plan)
    verify_inventory(compiled)
    assert set(compiled.capabilities) == LIVE_LETTINGS_CAPS
    assert compiled.missing_reuse == []
    assert "residential_lettings" in compiled.text
    assert LETTINGS.is_file()
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert compiled.intake["schema_version"] == "intake_blueprint.v1"
    assert compiled.template_revision == TEMPLATE_REVISION


def test_vetcare_fresh_session_compiles_on_the_new_path():
    """A VetCare-shaped plan gets one brief; missing REUSE is the named halt."""
    plan = _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE"),
        _Cap("automated_reminders", ["notification"], "REUSE"),
        _Cap("clinic_intake", [], "GENERATE"),
    )
    compiled = compile_brief(
        _Blueprint(),
        plan,
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    verify_inventory(compiled)
    assert "VetCare Hub" in compiled.text
    assert "appointment_scheduling" in compiled.text
    assert "clinic_intake" in compiled.text
    assert any(item.capability_id == "clinic_intake" and item.is_gap for item in compiled.inventory)
    assert compiled.missing_reuse == []

    poisoned = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "no_such_block"], "COMPOSE")),
        store_ids={"event_bus"},
    )
    with pytest.raises(InventoryHalt, match="no_such_block"):
        verify_inventory(poisoned)


def test_reuse_http_present_false_is_a_named_missing_id():
    """STEP 0 trusts the Blocks REUSE 200 body, not a local assumption."""
    from app.factory.build.reuse_lookup import ReuseRecord

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
        reuse_http_get=fake_get,
    )
    assert compiled.missing_reuse == ["event_bus"]
    assert compiled.reuse_records["event_bus"]["present"] is False
