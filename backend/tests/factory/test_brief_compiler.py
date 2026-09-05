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
    assert "writer_behaviour" in text
    assert "no capability accepted its own schema" in text
    assert "[check:writer_behaviour]" in text
    assert "test_every_capability_route_accepts_payload" in text
    assert "workflow: step_N (event_bus): error" in text
    assert "workflow: step_1 (event_bus): error" in text
    assert "workflow: step_2 (event_bus): error" in text
    assert "appointment_scheduling" in text
    assert "appointment_booking" in text
    assert "[check:event_bus_workflow]" in text
    assert "action=publish" in text
    assert "'input': payload" in text
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
    assert "writer_behaviour" in compiled.text
    assert "no capability accepted its own schema" in compiled.text
    assert "test_every_capability_route_accepts_payload" in compiled.text
    assert "workflow: step_N (event_bus): error" in compiled.text
    assert "workflow: step_2 (event_bus): error" in compiled.text
    assert "appointment_booking" in compiled.text
    assert "action=publish" in compiled.text
    assert "payload dict" in compiled.text
    assert "input.topic" in compiled.text
    assert "'input': payload" in compiled.text
    assert '"channel": "mcp"' in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert any(item.capability_id == "clinic_intake" and item.is_gap for item in compiled.inventory)
    assert compiled.missing_reuse == []

    poisoned = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "no_such_block"], "COMPOSE")),
        store_ids={"event_bus"},
    )
    with pytest.raises(InventoryHalt, match="no_such_block"):
        verify_inventory(poisoned)


def test_reuse_http_present_false_drops_invented_reuse_claim(monkeypatch):
    """STEP 0 trusts the Blocks exact-id 200 body, not a local shelf hit.

    CI has no CEREBRUM_API_URL (unlike a local .env). The injected getter
    must still run. present:false is an invented REUSE claim — drop it to
    a named GAP. Do not HALT the session; do not keep the REUSE line.
    """
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
        reuse_http_get=fake_get,
    )
    assert compiled.missing_reuse == []
    assert compiled.reuse_records["event_bus"]["present"] is False
    item = compiled.inventory[0]
    assert item.dropped_reuse == ["event_bus"]
    assert item.verified_present == []
    assert item.missing == []
    assert item.is_gap
    verify_inventory(compiled)
    assert "unverified REUSE dropped" in compiled.text
    assert "Store exact-id present=false" in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    reuse_section = compiled.text.split("REUSE (verified present):", 1)[1].split(
        "GAPS (you author", 1
    )[0]
    assert "event_bus" not in reuse_section


def test_planted_reuse_claim_for_absent_id_still_rejects():
    """Mutation: a brief that still claims REUSE for a ghost id is refused."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("clinic_intake", [], "GENERATE")),
        store_ids={"event_bus"},
    )
    verify_inventory(compiled)
    compiled.missing_reuse = ["not_a_real_block"]
    compiled.inventory[0].missing = ["not_a_real_block"]
    compiled.inventory[0].strategy = "REUSE"
    with pytest.raises(InventoryHalt, match="not_a_real_block"):
        verify_inventory(compiled)
    planted = lint_brief(compiled)
    assert planted.ok is False
    assert any("unresolved block id" in e for e in planted.errors)


def test_reuse_http_l2_fields_appear_in_brief_when_declared(monkeypatch):
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(
            block_id=block_id,
            present=True,
            source="registry/blocks",
            reads=["topic"],
            writes=["event"],
            never=["channel"],
            acceptance=["publish succeeds"],
            scope_declared=True,
        )

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
        reuse_http_get=fake_get,
    )
    verify_inventory(compiled)
    assert "topic" in compiled.text
    assert "publish succeeds" in compiled.text
    assert not any(
        "event_bus" in line and "pre-flip" in line
        for line in compiled.text.splitlines()
    )
    assert any(
        "event_bus" in line and "READS=" in line and "topic" in line
        for line in compiled.text.splitlines()
    )
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_preflip_block_json_says_scopes_not_declared():
    """Vendor-mirror block.json has no L2.2 keys — brief must say so, not invent."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("automated_reminders", ["notification"], "REUSE")),
        store_ids={"notification"},
    )
    verify_inventory(compiled)
    assert compiled.reuse_records["notification"]["present"] is True
    assert compiled.reuse_records["notification"]["scope_declared"] is False
    assert compiled.reuse_records["notification"]["reads"] == []
    assert "not declared on block.json (pre-flip)" in compiled.text
    assert "do not invent scopes" in compiled.text
    # Must not invent a clinic/reminder scope the mirror never declared.
    assert "appointment_slot" not in compiled.text
    assert "sms_body" not in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_compiler_does_not_invent_scopes_when_http_omits_l2(monkeypatch):
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(
            block_id=block_id,
            present=True,
            source="registry/blocks",
            scope_declared=False,
        )

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("automated_reminders", ["notification"], "REUSE")),
        store_ids={"notification"},
        reuse_http_get=fake_get,
    )
    assert "not declared on block.json (pre-flip)" in compiled.text
    rec = compiled.reuse_records["notification"]
    assert rec["reads"] == []
    assert rec["writes"] == []
    assert rec["never"] == []
    assert rec["acceptance"] == []


def test_case_sensitive_claimed_reuse_does_not_match_folded_id():
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("docs", ["DocumentEngine"], "REUSE")),
        store_ids={"document_engine"},
    )
    assert compiled.missing_reuse == ["DocumentEngine"]
    with pytest.raises(InventoryHalt, match="DocumentEngine"):
        verify_inventory(compiled)


def test_vetcare_invented_readiness_engine_reuse_dropped_on_store_miss(monkeypatch):
    """Live sess_8ef2f7b7ae594b85: architect claimed readiness_engine REUSE.

    The Factory vendor mirror / dual-registry lists readiness_engine (estate
    property-readiness). The Blocks exact-id surface does not. STEP 0 must
    drop the claim — veterinarian_availability_tracking is a GAP — not HALT
    before WRITER with BRIEF_LINT_REJECTED.
    """
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            _Cap(
                "veterinarian_availability_tracking",
                ["readiness_engine"],
                "REUSE",
            )
        ),
        store_ids={"readiness_engine", "event_bus"},
        reuse_http_get=fake_get,
    )
    assert compiled.missing_reuse == []
    item = compiled.inventory[0]
    assert item.capability_id == "veterinarian_availability_tracking"
    assert item.dropped_reuse == ["readiness_engine"]
    assert item.block_ids == []
    assert item.verified_present == []
    assert item.missing == []
    assert item.is_gap
    verify_inventory(compiled)
    result = lint_brief(compiled)
    assert result.ok, result.errors
    assert "unverified REUSE dropped" in compiled.text
    assert "readiness_engine" in compiled.text
    reuse_section = compiled.text.split("REUSE (verified present):", 1)[1].split(
        "GAPS (you author", 1
    )[0]
    assert "readiness_engine" not in reuse_section
    assert "veterinarian_availability_tracking:readiness_engine" not in compiled.text


def test_vetcare_readiness_engine_reuse_kept_when_exact_id_hits(monkeypatch):
    """If the Store exact-id endpoint confirms the id, REUSE stands."""
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=True, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            _Cap(
                "veterinarian_availability_tracking",
                ["readiness_engine"],
                "REUSE",
            )
        ),
        store_ids={"readiness_engine"},
        reuse_http_get=fake_get,
    )
    verify_inventory(compiled)
    item = compiled.inventory[0]
    assert item.verified_present == ["readiness_engine"]
    assert item.dropped_reuse == []
    assert item.missing == []
    assert item.is_reuse
    assert compiled.missing_reuse == []
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert "REUSE ['readiness_engine']" in compiled.text
