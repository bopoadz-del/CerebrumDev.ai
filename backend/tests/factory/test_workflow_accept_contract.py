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

import pytest

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
    PREPARED_EVENT_BUS_STEP_EXAMPLE,
    PRODUCT_ACCEPT_CHECK,
    PRODUCT_ACCEPT_TEST,
    PRODUCT_EMAIL_SAMPLE,
    PRODUCT_EVENT_BUS_STEP_CLASS,
    PRODUCT_EVENT_BUS_STEP_HALT,
    WRITER_EVENT_BUS_WORKFLOW_HALT,
    EventBusWorkflowHalt,
    assert_event_bus_workflow_handlers,
    declares_event_bus_workflow,
    event_bus_workflow_capability_ids,
    event_bus_workflow_handler_errors,
    handler_has_prepared_event_bus_step,
    handler_satisfies_event_bus_contract,
    workflow_accept_acceptance_line,
    workflow_accept_brief_contract,
    workflow_accept_forbidden_lines,
    workflow_accept_needles,
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


PREPARED_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{\n"
    "        'block': 'event_bus',\n"
    "        'action': 'publish',\n"
    "        'input': {\n"
    "            'topic': 'appointment.scheduled',\n"
    "            'payload': {'reference': payload.get('reference')},\n"
    "            'message': 'appointment recorded',\n"
    "            'channel': 'mcp',\n"
    "        },\n"
    "    }]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

UNPREPARED_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{'block': 'event_bus', 'input': payload}]\n"
    "    return execute('workflow', {'steps': steps})\n"
)


def _vetcare_plan():
    return _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE"),
        _Cap("reminders_and_notifications", ["notification", "workflow", "event_bus"], "COMPOSE"),
        _Cap("reminders_notifications", ["notification", "workflow", "event_bus"], "COMPOSE"),
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
    assert "reminders_notifications" in ids
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
    assert "appointment_scheduling" in text
    assert "reminders_notifications" in text
    assert PREPARED_EVENT_BUS_STEP_EXAMPLE in text
    assert "'input': payload" in text
    assert '"channel": "mcp"' in text
    assert '"action": "publish"' in text
    assert "input.topic" in text
    assert "payload dict" in text
    rules = workflow_accept_rules_text(
        capability_ids=["appointment_scheduling", "reminders_notifications"]
    )
    assert PRODUCT_ACCEPT_TEST in rules
    assert "appointment_scheduling" in rules
    assert "reminders_notifications" in rules
    assert PREPARED_EVENT_BUS_STEP_EXAMPLE in rules
    assert workflow_accept_forbidden_lines() in text
    for needle in workflow_accept_needles():
        assert needle.lower() in text.lower(), needle
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
    assert "'input': payload" in contract
    assert "appointment_scheduling" in contract
    assert "reminders_notifications" in contract
    assert contract in CODING_AGENT_BRIEF
    assert PRODUCT_ACCEPT_TEST in _WHOLE_JOB_SYSTEM
    assert PRODUCT_EVENT_BUS_STEP_HALT in _WHOLE_JOB_SYSTEM
    assert "channel=mcp" in _WHOLE_JOB_SYSTEM
    assert "'input': payload" in _WHOLE_JOB_SYSTEM or "input to payload" in _WHOLE_JOB_SYSTEM


def test_appointment_style_with_event_bus_only_still_gets_the_contract():
    """CLI invents workflow children even when the plan only bound event_bus."""
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_scheduling", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
    )
    assert "appointment_scheduling" in event_bus_workflow_capability_ids(compiled)
    assert declares_event_bus_workflow(compiled) is True
    assert PREPARED_EVENT_BUS_STEP_EXAMPLE in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_prepared_handler_contract_helpers():
    assert handler_has_prepared_event_bus_step(PREPARED_HANDLER) is True
    assert handler_satisfies_event_bus_contract(PREPARED_HANDLER) is True
    assert handler_has_prepared_event_bus_step(UNPREPARED_HANDLER) is False
    assert handler_satisfies_event_bus_contract(UNPREPARED_HANDLER) is False
    wrapped = "from app.block_inputs import prepare_block_input\n" + UNPREPARED_HANDLER
    assert handler_satisfies_event_bus_contract(wrapped) is True


def test_harness_fails_unprepared_event_bus_handlers_before_writer_done(tmp_path):
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        UNPREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "reminders_notifications.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_scheduling" in e for e in errors)
    assert not any("reminders_notifications" in e for e in errors)
    with pytest.raises(EventBusWorkflowHalt, match=WRITER_EVENT_BUS_WORKFLOW_HALT):
        assert_event_bus_workflow_handlers(tmp_path, compiled)


def test_harness_passes_prepared_and_wrapped_handlers(tmp_path):
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "reminders_and_notifications.py").write_text(
        "from app.block_inputs import prepare_block_input\n" + UNPREPARED_HANDLER,
        encoding="utf-8",
    )
    (actions / "reminders_notifications.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    assert event_bus_workflow_handler_errors(tmp_path, compiled) == []
    assert_event_bus_workflow_handlers(tmp_path, compiled)
