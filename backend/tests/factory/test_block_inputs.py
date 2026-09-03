"""Accept-payload contracts for the blocks that failed residential-lettings pilot.

Live Platforms card (hash b36090a424db) refused export because
``test_every_capability_route_accepts_payload`` collected:

1. unit_registry — Missing required field: property_reference_code
2. notification — missing channel/message
3. team — NoneType has no attribute lower
4. workflow — missing steps
5. document_engine — expected str/bytes, got dict

These tests lock the shared ``prepare_block_input`` / sample-payload helpers
so NEW platforms inherit the fix. They do not weaken the pilot suite.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app.factory.build.block_inputs import (
    align_spec_to_handler_fields,
    handler_required_fields,
    prepare_block_input,
    render_block_inputs_module,
)
from app.factory.build.roles import (
    _constraint_guard,
    _handler_module,
    _sample_payload,
    _templated_body,
)
from app.factory.build.roles_constants import _DISPATCH_RUNTIME


# -- notification ---------------------------------------------------------


def test_notification_payload_has_channel_and_message():
    """Caller domain record must not need channel/message (coder contract)."""
    domain = {"reference": "V1", "status": "open", "quantity": 1}
    out = prepare_block_input("notification", domain, roster=["notification", "team"])
    assert out["channel"] == "mcp"
    assert isinstance(out["message"], str) and out["message"]
    assert out["block"] == "team"
    # Domain columns preserved for adapters that fold them into input.
    assert out["reference"] == "V1"


def test_notification_does_not_overwrite_caller_supplied_fields():
    domain = {"channel": "mcp", "message": "hello", "block": "database"}
    out = prepare_block_input("notification", domain, roster=["notification"])
    assert out["message"] == "hello"
    assert out["block"] == "database"


# -- workflow -------------------------------------------------------------


def test_workflow_payload_has_steps_built_from_roster():
    domain = {"reference": "T1", "status": "open"}
    out = prepare_block_input(
        "workflow", domain, roster=["workflow", "database", "notification"]
    )
    assert isinstance(out["steps"], list) and out["steps"]
    assert out["steps"][0]["block"] == "database"
    assert "block_id" not in out["steps"][0]


def test_workflow_keeps_explicit_steps():
    steps = [{"block": "team", "input": {"user_id": "u1"}}]
    out = prepare_block_input("workflow", {"steps": steps}, roster=["workflow", "team"])
    assert out["steps"] == steps


# -- team -----------------------------------------------------------------


def test_team_payload_never_passes_none_to_lowercased_fields():
    domain = {"name": None, "slug": None, "role": None, "user_id": None}
    out = prepare_block_input("team", domain, product_name="Residential Lettings")
    for key in ("user_id", "name", "slug"):
        assert isinstance(out[key], str) and out[key]
        out[key].lower()  # must not raise — the live AttributeError


def test_team_injects_resource_id_when_preconditions_exist(monkeypatch):
    fake = types.ModuleType("app.preconditions")
    fake.resource_id = lambda block_id: "team_abc123" if block_id == "team" else None
    monkeypatch.setitem(sys.modules, "app.preconditions", fake)
    out = prepare_block_input("team", {"reference": "x"})
    assert out["team_id"] == "team_abc123"


# -- document_engine ------------------------------------------------------


def test_document_engine_rejects_dict_path_and_supplies_text():
    """Live TypeError: expected str/bytes/PathLike, got dict."""
    domain = {
        "reference": "A1",
        "file_path": {"name": "lease.pdf", "meta": True},
        "attachment_path": {"nested": True},
    }
    out = prepare_block_input("document_engine", domain)
    for key in ("file_path", "pdf_path", "attachment_path", "path"):
        if key in out:
            assert isinstance(out[key], (str, bytes)), (key, out[key])
    assert isinstance(out.get("text"), str) and out["text"]


def test_document_engine_keeps_string_attachment_path():
    domain = {"attachment_path": "/tmp/lease.pdf", "reference": "A1"}
    out = prepare_block_input("document_engine", domain)
    assert out["attachment_path"] == "/tmp/lease.pdf"
    assert out["file_path"] == "/tmp/lease.pdf"
    assert out["pdf_path"] == "/tmp/lease.pdf"


# -- analytics (historical envelope miss) ---------------------------------


def test_analytics_lifts_metric_value_and_defaults():
    out = prepare_block_input(
        "analytics",
        {"input": {"metric": "monthly_rent_gbp", "value": 1450.0}, "status": "vacant"},
    )
    assert out["metric"] == "monthly_rent_gbp"
    assert out["value"] == 1450.0


# -- property_reference_code / sample ↔ handler alignment -----------------


def test_handler_required_fields_are_mined_from_error_strings():
    body = (
        "    if 'property_reference_code' not in payload:\n"
        "        return {'ok': False, 'error': 'Missing required field: property_reference_code'}\n"
    )
    assert "property_reference_code" in handler_required_fields(body)


def test_align_spec_adds_handler_required_field_for_sample_payload():
    spec = {
        "entity": "unit",
        "fields": [
            {"name": "unit_reference", "type": "str", "required": True},
            {"name": "status", "type": "str", "required": True,
             "allowed_values": ["vacant", "occupied"]},
        ],
    }
    aligned, added = align_spec_to_handler_fields(spec, ["property_reference_code"])
    assert added == ["property_reference_code"]
    payload = _sample_payload(aligned)
    assert "property_reference_code" in payload
    assert isinstance(payload["property_reference_code"], str)
    assert payload["status"] == "vacant"


def test_constraint_guard_enforces_required_fields_from_spec():
    spec = {
        "fields": [
            {"name": "property_reference_code", "type": "str", "required": True},
            {"name": "status", "type": "str", "required": True,
             "allowed_values": ["vacant", "occupied"]},
        ]
    }
    body = _constraint_guard(spec)
    ns: dict = {}
    exec("def handle(payload):\n" + body + "\n    return {'ok': True}\n", ns)
    refused = ns["handle"]({"status": "vacant"})
    assert refused["ok"] is False
    assert "property_reference_code" in refused["error"]
    accepted = ns["handle"](
        {"property_reference_code": "P-1", "status": "vacant"}
    )
    assert accepted["ok"] is True


# -- emitted module + wrapper end-to-end ----------------------------------


def test_rendered_block_inputs_module_is_importable(tmp_path):
    path = tmp_path / "block_inputs.py"
    path.write_text(render_block_inputs_module(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("gen_block_inputs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.prepare_block_input(
        "notification", {"reference": "x"}, roster=["notification", "workflow"]
    )
    assert out["channel"] == "mcp" and out["message"]


def test_templated_handler_wrapper_prepares_notification_before_execute(tmp_path):
    """Even a naive ``execute(block_id, payload)`` body must send channel/message."""
    module_text = _handler_module(
        "viewing_management",
        ["notification", "workflow", "team"],
        _templated_body(["notification", "workflow", "team"]),
        "deterministic contract template",
        {
            "notification": "send",
            "workflow": "run",
            "team": "get_team_context",
        },
    )
    # Provide the generated helper the wrapper imports without replacing the
    # real ``app`` package (that would break later tests' ``app.factory``).
    block_inputs_mod = types.ModuleType("app.block_inputs")
    exec(render_block_inputs_module(), block_inputs_mod.__dict__)
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []
    fake_dispatch = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        calls.append({"block_id": block_id, "payload": dict(payload), "action": action})
        return {"status": "ok", "block": block_id}

    fake_dispatch.execute = _fake_execute
    previous_dispatch = sys.modules.get("app.dispatch")
    previous_block_inputs = sys.modules.get("app.block_inputs")
    sys.modules["app.dispatch"] = fake_dispatch
    sys.modules["app.block_inputs"] = block_inputs_mod
    try:
        spec = importlib.util.spec_from_file_location("viewing_handler", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"reference": "V9", "status": "open", "quantity": 2})
    finally:
        if previous_dispatch is None:
            sys.modules.pop("app.dispatch", None)
        else:
            sys.modules["app.dispatch"] = previous_dispatch
        if previous_block_inputs is None:
            sys.modules.pop("app.block_inputs", None)
        else:
            sys.modules["app.block_inputs"] = previous_block_inputs

    assert out.get("ok") is True, out
    by_block = {c["block_id"]: c["payload"] for c in calls}
    assert by_block["notification"]["channel"] == "mcp"
    assert by_block["notification"]["message"]
    assert isinstance(by_block["workflow"]["steps"], list) and by_block["workflow"]["steps"]
    assert isinstance(by_block["team"]["user_id"], str)
    assert isinstance(by_block["team"]["name"], str)
    assert isinstance(by_block["team"]["slug"], str)


def test_dispatch_runtime_still_has_no_f18_fabrication_helpers():
    """Construction lives in block_inputs, never in dispatch (LotDesk F18)."""
    assert "_default_block_field" not in _DISPATCH_RUNTIME
    assert "_ALWAYS_FILL" not in _DISPATCH_RUNTIME
    assert "_ensure_offline_block_input" not in _DISPATCH_RUNTIME
    assert "prepare_block_input" not in _DISPATCH_RUNTIME


def test_sample_payload_covers_every_required_field_type():
    spec = {
        "fields": [
            {"name": "property_reference_code", "type": "str", "required": True},
            {"name": "channel_note", "type": "str", "required": False},
            {"name": "beds", "type": "int", "required": True, "min": 1, "max": 8},
            {"name": "furnished", "type": "bool", "required": True},
        ]
    }
    payload = _sample_payload(spec)
    assert set(payload) == {
        "property_reference_code",
        "channel_note",
        "beds",
        "furnished",
    }
    assert isinstance(payload["property_reference_code"], str)
    assert payload["beds"] == 1
    assert isinstance(payload["furnished"], bool)


def test_field_ops_role_runner_emits_block_inputs_and_pilot_accepts(tmp_path, monkeypatch):
    """End-to-end: the blueprint that binds notification/workflow/team/analytics.

    Residential-lettings failed those shapes at pilot. A keyless field_ops
    build must emit ``app/block_inputs.py`` and pass
    ``test_every_capability_route_accepts_payload``.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    root = Path(__file__).resolve().parents[3]
    out = tmp_path / "field_ops_build"
    outcome = RoleRunner(load_blueprint(root / "blueprints/examples/field_ops.yaml"), out).run()
    assert outcome.ok, outcome.to_dict()
    assert (out / "app" / "block_inputs.py").is_file()
    handler = (out / "app" / "actions" / "stakeholder_notification.py").read_text(
        encoding="utf-8"
    )
    assert "prepare_block_input" in handler

    import os
    import subprocess
    import sys

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
    assert "passed" in proc.stdout
