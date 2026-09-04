"""C-BRIEF must ground PRODUCT event_bus workflow accept-payload.

Live sess_e94ddfa797ea4a45 (VetCare Hub / veterinary-care) on tip f090030
(#317 schema-accept after #316 C-BRIEF):

    PRODUCT (pilot-marked suite): suite is red: schema sample refused;
    schema sample refused (event_bus workflow step);
    accept-payload persisted nothing — FAILED

    reminders_and_notifications rejected a payload built from its own
    schema: workflow: step_2 (event_bus): error.

This is the same overnight PRODUCT wall class (#315 appointment_scheduling
event_bus step_1, then step_2). The compiled brief never named the
accept-payload / event_bus workflow contract, so FACTORY_CODE_CLI / HTTP
oneshot invented a mismatched workflow. This module is the compiler +
harness contract — not a VetCare product patch.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import brief_fingerprint, compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.roles_handlers import _sample_payload, _sample_value
from app.factory.build.schema_accept import (
    CHANNEL_SAMPLE,
    DATETIME_SAMPLE,
    DATE_SAMPLE,
    ENVELOPE_STATUS_SAMPLE,
    GENERIC_STR_SAMPLE,
    TIME_SAMPLE,
)
from app.factory.build.workflow_accept import (
    EVENT_BUS_STEP_ACTION,
    EVENT_BUS_STEP_CHANNEL,
    PRODUCT_ACCEPT_CHECK,
    PRODUCT_ACCEPT_TEST,
    PRODUCT_EMAIL_SAMPLE,
    PRODUCT_EVENT_BUS_STEP_CLASS,
    PRODUCT_EVENT_BUS_STEP_HALT,
    declares_event_bus_workflow,
    event_bus_workflow_capability_ids,
    workflow_accept_acceptance_line,
    workflow_accept_brief_contract,
    workflow_accept_rules_text,
)
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import _WHOLE_JOB_SYSTEM
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


class _VetCare:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def _vetcare_plan():
    return _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE"),
        _Cap("reminders_and_notifications", ["notification", "workflow", "event_bus"], "COMPOSE"),
        _Cap("clinic_intake", [], "GENERATE"),
    )


def test_product_sample_literals_match_tester_payload():
    """Drift between the brief contract and PRODUCT _sample_value is the next halt."""
    assert _sample_value({"name": "status", "type": "str"}) == ENVELOPE_STATUS_SAMPLE == "open"
    assert _sample_value({"name": "channel", "type": "str"}) == CHANNEL_SAMPLE == "email"
    assert _sample_value({"name": "pet_name", "type": "str"}) == GENERIC_STR_SAMPLE
    assert _sample_value({"name": "owner_email", "type": "str"}) == PRODUCT_EMAIL_SAMPLE
    assert _sample_value({"name": "created_at", "type": "str"}) == DATETIME_SAMPLE
    assert _sample_value({"name": "appointment_date", "type": "str"}) == DATE_SAMPLE
    assert _sample_value({"name": "scheduled_time", "type": "str"}) == TIME_SAMPLE
    sample = _sample_payload(
        {
            "fields": [
                {"name": "reference", "type": "str"},
                {"name": "status", "type": "str"},
                {"name": "channel", "type": "str"},
                {"name": "owner_email", "type": "str"},
            ]
        }
    )
    assert sample["status"] == "open"
    assert sample["channel"] == "email"
    assert sample["owner_email"] == PRODUCT_EMAIL_SAMPLE
    assert sample["channel"] != GENERIC_STR_SAMPLE


def test_vetcare_inventory_declares_event_bus_workflows():
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    ids = event_bus_workflow_capability_ids(compiled)
    assert "appointment_scheduling" in ids
    assert "reminders_and_notifications" in ids
    assert "clinic_intake" not in ids
    assert declares_event_bus_workflow(compiled) is True


def test_vetcare_compiled_brief_grounds_event_bus_workflow_accept():
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    text = compiled.text
    assert PRODUCT_ACCEPT_TEST in text
    assert PRODUCT_EVENT_BUS_STEP_HALT in text
    assert PRODUCT_EVENT_BUS_STEP_CLASS in text
    assert workflow_accept_acceptance_line() in text
    assert f"[check:{PRODUCT_ACCEPT_CHECK}]" in text
    assert EVENT_BUS_STEP_CHANNEL in text
    assert EVENT_BUS_STEP_ACTION in text
    assert "never the raw schema sample" in text
    rules = workflow_accept_rules_text()
    assert PRODUCT_ACCEPT_TEST in rules
    assert "reminders_and_notifications" in text or "appointment_scheduling" in text
    result = lint_brief(compiled)
    assert result.ok, result.errors


def test_lettings_golden_roster_and_fingerprint_unchanged():
    bp = draft_blueprint_from_brief(
        "build a platform for residential lettings",
        use_llm=False,
    )
    assert {c.id for c in bp.capabilities} == LIVE_LETTINGS_CAPS
    golden = load_blueprint(lettings_golden_path())
    assert {c.id for c in golden.capabilities} == LIVE_LETTINGS_CAPS
    plan = plan_blueprint(bp)
    compiled = compile_brief(bp, plan)
    fp = brief_fingerprint(compiled)
    assert set(fp["capabilities"]) == LIVE_LETTINGS_CAPS
    assert fp["missing_reuse"] == []
    assert declares_event_bus_workflow(compiled) is False
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert LETTINGS.is_file()


def test_lettings_and_smoke_still_lint_with_workflow_accept():
    for path in (SMOKE, LETTINGS):
        bp = load_blueprint(path)
        compiled = compile_brief(bp, plan_blueprint(bp))
        assert PRODUCT_ACCEPT_TEST in compiled.text
        result = lint_brief(compiled)
        assert result.ok, (path.name, result.errors)


def test_system_brief_and_oneshot_name_the_event_bus_step_halt():
    contract = workflow_accept_brief_contract()
    assert PRODUCT_ACCEPT_TEST in contract
    assert PRODUCT_EVENT_BUS_STEP_HALT in contract
    assert EVENT_BUS_STEP_CHANNEL in contract
    assert contract in CODING_AGENT_BRIEF
    assert PRODUCT_ACCEPT_TEST in _WHOLE_JOB_SYSTEM
    assert PRODUCT_EVENT_BUS_STEP_HALT in _WHOLE_JOB_SYSTEM
    assert "channel=mcp" in _WHOLE_JOB_SYSTEM
