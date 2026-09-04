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

from app.factory.build.block_inputs import (
    align_spec_to_handler_fields,
    align_spec_to_handler_source,
    handler_field_contracts,
    handler_required_fields,
    prepare_block_input,
    render_block_inputs_module,
    split_execute_action,
)
from app.factory.build.roles import (
    _constraint_guard,
    _handler_module,
    _sample_payload,
    _sample_value,
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


def test_handler_required_fields_mine_plural_lists_and_is_missing():
    """Live VetConnect: handlers named several fields in one error string."""
    body = (
        "    needed = ['pet_name', 'owner_name', 'appointment_date', "
        "'veterinarian_name']\n"
        "    missing = [n for n in needed if n not in payload]\n"
        "    if missing:\n"
        "        return {'ok': False, 'error': 'Missing required fields: ' "
        "+ ', '.join(missing)}\n"
        "    if not payload.get('owner_id'):\n"
        "        return {'ok': False, 'error': 'owner_id is missing and "
        "must be a non-empty string'}\n"
    )
    mined = handler_required_fields(body)
    for name in (
        "pet_name",
        "owner_name",
        "appointment_date",
        "veterinarian_name",
        "owner_id",
    ):
        assert name in mined, (name, mined)
    assert "steps" not in mined
    assert "team_id" not in mined


def test_handler_required_fields_keeps_domain_status_and_channel():
    """``status`` / ``channel`` are domain columns on new-domain pilots.

    The envelope skip list used to drop them, so veterinarian availability
    ``status`` and reminder ``channel`` never reached the sample payload.
    """
    body = (
        "        return {'ok': False, 'error': 'Missing required fields: "
        "service_date, service_type, capacity, status'}\n"
        "        return {'ok': False, 'error': 'channel is invalid'}\n"
        "        if payload.get('channel') not in ('email', 'sms', 'push'):\n"
        "            return {'ok': False, 'error': 'channel is invalid'}\n"
    )
    mined = handler_required_fields(body)
    assert "status" in mined
    contracts = handler_field_contracts(body)
    assert "channel" in contracts
    assert contracts["channel"]["allowed_values"] == ["email", "sms", "push"]
    assert "ok" not in mined
    assert "error" not in mined
    assert "ok" not in contracts
    assert "steps" not in contracts


def test_handler_field_contracts_mine_vetconnect_enums_types_bounds():
    """Lock the 2026-09-04 Platforms card: enums, bool, int >= 0, non-empty ids."""
    body = (
        "    if 'clinic_id' not in payload or not payload.get('clinic_id'):\n"
        "        return {'ok': False, 'error': 'clinic_id is missing and "
        "must be a non-empty string'}\n"
        "    if payload.get('role') not in ('veterinarian', 'technician', "
        "'receptionist', 'practice_manager', 'pet_owner'):\n"
        "        return {'ok': False, 'error': 'role must be one of "
        "{veterinarian, technician, receptionist, practice_manager, "
        "pet_owner}'}\n"
        "    if payload.get('access_level') not in ('admin', 'standard', "
        "'read_only'):\n"
        "        return {'ok': False, 'error': 'access_level must be one of "
        "{admin, standard, read_only}'}\n"
        "    if not isinstance(payload.get('is_active'), bool):\n"
        "        return {'ok': False, 'error': 'is_active must be a boolean'}\n"
        "    if not isinstance(payload.get('login_count'), int) "
        "or payload.get('login_count') < 0:\n"
        "        return {'ok': False, 'error': 'login_count must be an "
        "integer >= 0'}\n"
        "    if payload.get('reminder_type') not in ('appointment', 'vaccine', "
        "'follow_up'):\n"
        "        return {'ok': False, 'error': 'reminder_type is invalid'}\n"
        "    if payload.get('channel') not in ('email', 'sms', 'push'):\n"
        "        return {'ok': False, 'error': 'channel is invalid'}\n"
        "    if not payload.get('message_template'):\n"
        "        return {'ok': False, 'error': 'message_template must be a "
        "non-empty string'}\n"
    )
    contracts = handler_field_contracts(body)
    assert contracts["role"]["allowed_values"][0] == "veterinarian"
    assert set(contracts["role"]["allowed_values"]) == {
        "veterinarian",
        "technician",
        "receptionist",
        "practice_manager",
        "pet_owner",
    }
    assert contracts["access_level"]["allowed_values"] == [
        "admin",
        "standard",
        "read_only",
    ]
    assert contracts["is_active"]["type"] == "bool"
    assert contracts["login_count"]["type"] == "int"
    assert contracts["login_count"]["min"] == 0
    assert contracts["clinic_id"]["required"] is True
    assert contracts["channel"]["allowed_values"] == ["email", "sms", "push"]
    assert contracts["reminder_type"]["allowed_values"] == [
        "appointment",
        "vaccine",
        "follow_up",
    ]
    assert "message_template" in contracts


def test_align_spec_merges_vocab_and_types_onto_bare_str_fields():
    """Existing spec fields must pick up handler enums — not stay bare str."""
    spec = {
        "entity": "dashboard",
        "fields": [
            {"name": "role", "type": "str", "required": True},
            {"name": "is_active", "type": "str", "required": True},
            {"name": "login_count", "type": "str", "required": False},
        ],
    }
    contracts = {
        "role": {
            "name": "role",
            "required": True,
            "type": "str",
            "allowed_values": [
                "veterinarian",
                "technician",
                "receptionist",
                "practice_manager",
                "pet_owner",
            ],
        },
        "is_active": {"name": "is_active", "required": True, "type": "bool"},
        "login_count": {
            "name": "login_count",
            "required": True,
            "type": "int",
            "min": 0,
        },
        "clinic_id": {"name": "clinic_id", "required": True, "type": "str"},
    }
    aligned, changed = align_spec_to_handler_fields(
        spec, ["clinic_id"], contracts=contracts
    )
    assert "clinic_id" in changed
    assert "role" in changed
    by_name = {f["name"]: f for f in aligned["fields"]}
    payload = _sample_payload(aligned)
    assert payload["role"] == "veterinarian"
    assert payload["is_active"] is True
    assert isinstance(payload["login_count"], int) and payload["login_count"] >= 0
    assert payload["clinic_id"]
    assert by_name["login_count"]["type"] == "int"
    assert by_name["is_active"]["type"] == "bool"


def test_align_spec_to_handler_source_covers_vetconnect_caps():
    """Synthetic multi-capability VetConnect miss → sample satisfies guards."""
    handlers = {
        "appointment_scheduling": (
            "    needed = ['pet_name', 'owner_name', 'appointment_date', "
            "'veterinarian_name']\n"
            "    missing = [n for n in needed if n not in payload "
            "or payload[n] in (None, '')]\n"
            "    if missing:\n"
            "        return {'ok': False, 'error': 'Missing required fields: ' "
            "+ ', '.join(missing)}\n"
            "    return {'ok': True}\n"
        ),
        "veterinarian_availability_management": (
            "    required = ['service_date', 'service_type', 'capacity', 'status']\n"
            "    missing = [n for n in required if n not in payload "
            "or payload[n] in (None, '')]\n"
            "    if missing:\n"
            "        return {'ok': False, 'error': 'Missing required fields: "
            "service_date, service_type, capacity, status'}\n"
            "    return {'ok': True}\n"
        ),
        "pet_records": (
            "    if not payload.get('owner_id'):\n"
            "        return {'ok': False, 'error': 'owner_id is missing and "
            "must be a non-empty string'}\n"
            "    return {'ok': True}\n"
        ),
        "automated_reminders": (
            "    if not payload.get('pet_id'):\n"
            "        return {'ok': False, 'error': 'pet_id is missing and "
            "must be a non-empty string'}\n"
            "    if payload.get('reminder_type') not in ('appointment', 'vaccine'):\n"
            "        return {'ok': False, 'error': 'reminder_type is invalid'}\n"
            "    if payload.get('channel') not in ('email', 'sms'):\n"
            "        return {'ok': False, 'error': 'channel is invalid'}\n"
            "    if not payload.get('message_template'):\n"
            "        return {'ok': False, 'error': 'message_template must be a "
            "non-empty string'}\n"
            "    return {'ok': True}\n"
        ),
        "secure_multi_user_dashboard": (
            "    if not payload.get('clinic_id'):\n"
            "        return {'ok': False, 'error': 'clinic_id is missing and "
            "must be a non-empty string'}\n"
            "    if payload.get('role') not in ('veterinarian', 'technician', "
            "'receptionist', 'practice_manager', 'pet_owner'):\n"
            "        return {'ok': False, 'error': 'role must be one of "
            "{veterinarian, technician, receptionist, practice_manager, "
            "pet_owner}'}\n"
            "    if payload.get('access_level') not in ('admin', 'standard', "
            "'read_only'):\n"
            "        return {'ok': False, 'error': 'access_level must be one of "
            "{admin, standard, read_only}'}\n"
            "    if not isinstance(payload.get('is_active'), bool):\n"
            "        return {'ok': False, 'error': 'is_active must be a boolean'}\n"
            "    if not isinstance(payload.get('login_count'), int) "
            "or payload['login_count'] < 0:\n"
            "        return {'ok': False, 'error': 'login_count must be an "
            "integer >= 0'}\n"
            "    return {'ok': True}\n"
        ),
    }
    thin = {
        "entity": "record",
        "fields": [
            {"name": "reference", "type": "str", "required": True},
        ],
    }
    for cid, source in handlers.items():
        aligned, changed = align_spec_to_handler_source(thin, source)
        assert changed, cid
        payload = _sample_payload(aligned)
        ns: dict = {"payload": None}

        def _run(body: str, sample: dict, env: dict) -> dict:
            env = dict(env)
            exec("def handle(payload):\n" + body, env)
            return env["handle"](sample)

        out = _run(source, payload, ns)
        assert out.get("ok") is True, (cid, payload, out)
        guard = _constraint_guard(aligned)
        gns: dict = {}
        exec("def handle(payload):\n" + guard + "\n    return {'ok': True}\n", gns)
        guarded = gns["handle"](payload)
        assert guarded.get("ok") is True, (cid, payload, guarded)


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
    action, clean = mod.split_execute_action(
        {"action": "render", "name": "lathe"}, default_action="draw"
    )
    assert action == "render" and "action" not in clean


def test_split_execute_action_lifts_and_strips_payload_action():
    """Live makerspace: action in the dict must become the keyword and leave."""
    action, clean = split_execute_action(
        {"action": "render", "name": "lathe", "status": "open"},
        action=None,
        default_action="draw",
    )
    assert action == "render"
    assert "action" not in clean
    assert clean["name"] == "lathe"

    action, clean = split_execute_action(
        {"input": {"action": "insert", "table": "tools"}},
        action=None,
    )
    assert action == "insert"
    assert "action" not in clean
    assert "action" not in clean["input"]
    assert clean["input"]["table"] == "tools"

    action, clean = split_execute_action(
        {"action": "buried", "metric": "x"},
        action="track_event",
    )
    assert action == "track_event"
    assert "action" not in clean

    action, clean = split_execute_action({"name": "press"}, default_action="register")
    assert action == "register"
    assert "action" not in clean


def test_prepare_block_input_never_forwards_action_field():
    out = prepare_block_input(
        "estate_registry",
        {"action": "register", "name": "mill"},
        action="register",
    )
    assert "action" not in out
    assert out["name"] == "mill"


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
    for call in calls:
        assert "action" not in call["payload"], call
        assert call["action"], call


def test_handler_wrapper_lifts_action_out_of_payload_before_dispatch(tmp_path):
    """Regression: execute(block, {action: ...}) must become action= keyword.

    Live Your Platforms findings for makerspace-management named
    ``the action travelled inside the payload`` on dashboard/analytics/
    database/estate_registry. The wrapper is the last seam before
    dispatch; if action still rides in the payload here, WRITER halts.
    """
    buried = (
        "    results = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        body = dict(payload)\n"
        "        body['action'] = BLOCK_DEFAULT_ACTIONS.get(block_id) or 'run'\n"
        "        results[block_id] = execute(block_id, body)\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID, 'results': results}\n"
    )
    module_text = _handler_module(
        "dashboards_and_reports",
        ["dashboard", "analytics", "database"],
        buried,
        "coder LLM (makerspace live miss)",
        {
            "dashboard": "render",
            "analytics": "track_event",
            "database": "insert",
        },
    )
    block_inputs_mod = types.ModuleType("app.block_inputs")
    exec(render_block_inputs_module(), block_inputs_mod.__dict__)
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []
    fake_dispatch = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        assert "action" not in (payload or {}), (block_id, payload)
        if isinstance(payload, dict) and isinstance(payload.get("input"), dict):
            assert "action" not in payload["input"], (block_id, payload)
        assert action, (block_id, payload)
        calls.append({"block_id": block_id, "payload": dict(payload), "action": action})
        return {"status": "ok", "block": block_id}

    fake_dispatch.execute = _fake_execute
    previous_dispatch = sys.modules.get("app.dispatch")
    previous_block_inputs = sys.modules.get("app.block_inputs")
    sys.modules["app.dispatch"] = fake_dispatch
    sys.modules["app.block_inputs"] = block_inputs_mod
    try:
        spec = importlib.util.spec_from_file_location("makerspace_handler", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"name": "lathe", "status": "open"})
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
    assert {c["block_id"] for c in calls} == {"dashboard", "analytics", "database"}
    by_block = {c["block_id"]: c["action"] for c in calls}
    assert by_block == {
        "dashboard": "render",
        "analytics": "track_event",
        "database": "insert",
    }


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


def test_sample_value_uses_truthy_int_when_min_is_zero():
    """``min=0`` is valid; 0 is falsy and fails ``if not payload.get(...)``."""
    assert _sample_value({"name": "login_count", "type": "int", "min": 0}) == 1
    assert _sample_value({"name": "owner_id", "type": "str"}) == "id-1"


def test_tester_late_aligns_vetconnect_handlers_into_accept_payload(tmp_path):
    """Pilot rework must refresh samples from on-disk handler contracts."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, run_tester
    from app.factory.build.workspace import RoleWorkspace

    class _Cap:
        def __init__(self, cid):
            self.capability_id = cid
            self.block_ids = ()

    class _Plan:
        capabilities = (_Cap("secure_multi_user_dashboard"),)

    class _Blueprint:
        product_name = "VetConnect"
        product_id = "veterinary-services"
        vertical = "veterinary_services"

    root = tmp_path / "ws"
    handler = root / "app" / "actions" / "secure_multi_user_dashboard.py"
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text(
        "def handle(payload):\n"
        "    if not payload.get('clinic_id'):\n"
        "        return {'ok': False, 'error': 'clinic_id is missing and "
        "must be a non-empty string'}\n"
        "    if payload.get('role') not in ('veterinarian', 'technician', "
        "'receptionist', 'practice_manager', 'pet_owner'):\n"
        "        return {'ok': False, 'error': 'role must be one of "
        "{veterinarian, technician, receptionist, practice_manager, "
        "pet_owner}'}\n"
        "    if payload.get('access_level') not in ('admin', 'standard', "
        "'read_only'):\n"
        "        return {'ok': False, 'error': 'access_level must be one of "
        "{admin, standard, read_only}'}\n"
        "    if not isinstance(payload.get('is_active'), bool):\n"
        "        return {'ok': False, 'error': 'is_active must be a boolean'}\n"
        "    if not isinstance(payload.get('login_count'), int) "
        "or payload['login_count'] < 0:\n"
        "        return {'ok': False, 'error': 'login_count must be an "
        "integer >= 0'}\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    ws = RoleWorkspace(BuildRole.TESTER, root)
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        work_list=(
            "secure_multi_user_dashboard rejected a payload built from "
            "its own schema: clinic_id is missing",
        ),
        state={
            "build_cycle": "pilot",
            "vendored_blocks": (),
            "model_specs": {
                "secure_multi_user_dashboard": {
                    "entity": "dashboard",
                    "fields": [
                        {"name": "role", "type": "str", "required": True},
                        {"name": "is_active", "type": "str", "required": True},
                    ],
                }
            },
        },
    )
    result = run_tester(ctx)
    assert result.ok, result.detail
    routes = ws.read_text(Path("tests") / "test_routes.py")
    assert "test_every_capability_route_accepts_payload" in routes
    assert "'role': 'veterinarian'" in routes
    assert "'access_level': 'admin'" in routes
    assert "'is_active': True" in routes
    assert "'clinic_id': 'id-1'" in routes
    assert "login_count" in routes
    assert "'role': 'sample'" not in routes


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
