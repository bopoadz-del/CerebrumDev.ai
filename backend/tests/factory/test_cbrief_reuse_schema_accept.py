"""C-BRIEF: REUSE keep-path handlers must accept their own schema sample.

Photographed Floor after #345 (tip 666659a, sess_78483eaf7acd4219,
VetCare Hub): ModuleNotFoundError did not recur. WRITER reached 5/5
routes via FACTORY_CODE_CLI_BILLING + FACTORY_CODE_CLI_REUSE keep-path.
TESTER PRODUCT then failed after rework budget 3:

    patient_records_management: database: Unknown action;
      validation: Unknown action: None
    appointment_scheduling: workflow: step_0 (event_bus): error
    prescription_management: validation: Unknown action: None
    billing_and_invoicing: analytics: Unknown action: None
    client_communication_portal: team: Unknown action: None
    Also: schema sample refused (event_bus workflow step);
    accept-payload persisted nothing.

Keep-path emit wrote BLOCK_DEFAULT_ACTIONS = {}. This is compiler +
emit + harvest + WRITER halt — not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief, verify_inventory
from app.factory.build.brief_lint import lint_brief
from app.factory.build.coder_session import (
    KEEP_PATH_FACTORY_GROUNDED_REUSE,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_FAILED,
    emit_factory_grounded_reuse_keep_path,
)
from app.factory.build.reuse_accept import (
    LIVE_VETCARE_REUSE_ACCEPT_BLOCKS,
    LIVE_VETCARE_REUSE_ACCEPT_CAPS,
    PRODUCT_UNKNOWN_ACTION_HALT,
    PRODUCT_UNKNOWN_ACTION_NONE_HALT,
    REUSE_ACCEPT_MISS,
    STORE_BLOCK_DEFAULT_ACTIONS,
    WRITER_REUSE_ACCEPT_HALT,
    ReuseAcceptHalt,
    apply_default_actions_to_handler,
    assert_reuse_schema_accept,
    default_action_from_block_json,
    default_block_action,
    harvest_block_default_actions,
    parse_handler_default_actions,
    reuse_accept_acceptance_line,
    reuse_accept_brief_contract,
    reuse_accept_handler_errors,
    reuse_accept_needles,
    reuse_accept_rules_text,
)
from app.factory.build.roles import RoleContext, RoleError, run_writer
from app.factory.build.roles_handlers import (
    _capability_handler_body,
    _handler_module,
)
from app.factory.build.workflow_accept import (
    EVENT_BUS_STEP_ACTION,
    PRODUCT_EVENT_BUS_STEP_0_HALT,
)
from app.factory.build.workspace import RoleWorkspace
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import _WHOLE_JOB_SYSTEM
from tests.factory.test_coder_session import _require_cli, _usable_kimi_toml


STORE_IDS = {
    "database",
    "validation",
    "event_bus",
    "workflow",
    "analytics",
    "team",
}


class _Cap:
    def __init__(self, cid, block_ids, strategy="REUSE"):
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
    summary = "sess_78483eaf7acd4219 photograph — REUSE schema-sample accept"


def _vetcare_reuse_plan() -> _Plan:
    return _Plan(
        *(_Cap(cid, bids) for cid, bids in LIVE_VETCARE_REUSE_ACCEPT_BLOCKS.items())
    )


def _billing_cli(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo '429 Too Many Requests — this account has been suspended "
        "due to insufficient balance'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _arm_billing_cli(tmp_path: Path, monkeypatch) -> None:
    script = _billing_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)


def _plant_store_block_json(root: Path) -> None:
    """Simulate CLONER: vendored block.json with action defaults."""
    planted = {
        "database": ("query", ["query", "insert"]),
        "validation": ("validate", ["validate", "check"]),
        "event_bus": ("publish", ["publish", "notify"]),
        "workflow": ("run", ["run"]),
        "analytics": ("track_event", ["track_event"]),
        "team": ("create_team", ["create_team", "invite_member"]),
    }
    for bid, (default, options) in planted.items():
        dest = root / "vendor" / "blocks" / bid
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "block.json").write_text(
            json.dumps(
                {
                    "id": bid,
                    "inputs": [
                        {
                            "name": "action",
                            "type": "string",
                            "default": default,
                            "options": options,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )


def _schema_sample_exec_script(caps: Sequence[str]) -> str:
    return (
        "import importlib, sys, types\n"
        "sys.path.insert(0, '.')\n"
        "unknown = []\n"
        "calls = []\n"
        "def _execute(block_id, payload=None, action=None, params=None, **kw):\n"
        "    if not (isinstance(action, str) and action.strip()):\n"
        "        unknown.append('%s: Unknown action: %s' % (block_id, action))\n"
        "        return {'status': 'error', 'error': 'Unknown action: %s' % action}\n"
        "    calls.append((block_id, action))\n"
        "    return {'status': 'ok', 'block': block_id, 'action': action}\n"
        "dispatch = types.ModuleType('app.dispatch')\n"
        "dispatch.execute = _execute\n"
        "sys.modules['app.dispatch'] = dispatch\n"
        f"caps = {list(caps)!r}\n"
        "sample = {'reference': 'sample', 'status': 'open'}\n"
        "for cid in caps:\n"
        "    try:\n"
        "        mod = importlib.import_module('app.actions.' + cid)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        raise SystemExit('ModuleNotFoundError: ' + str(exc))\n"
        "    defaults = getattr(mod, 'BLOCK_DEFAULT_ACTIONS', {})\n"
        "    for bid in getattr(mod, 'BLOCK_IDS', []) or []:\n"
        "        action = defaults.get(bid) if isinstance(defaults, dict) else None\n"
        "        if not (isinstance(action, str) and action.strip()):\n"
        "            unknown.append('%s:%s: Unknown action: None' % (cid, bid))\n"
        "    if hasattr(mod, 'execute'):\n"
        "        mod.execute = _execute\n"
        "    try:\n"
        "        result = mod.handle(sample)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        raise SystemExit('ModuleNotFoundError: ' + str(exc))\n"
        "    except Exception as exc:\n"
        "        err = '%s: %s' % (type(exc).__name__, exc)\n"
        "        if 'Unknown action' in err:\n"
        "            unknown.append(cid + ': ' + err[:200])\n"
        "            result = {}\n"
        "        elif 'ModuleNotFound' in err or 'No module named' in err:\n"
        "            raise SystemExit(cid + ': ' + err)\n"
        "        else:\n"
        "            result = {}\n"
        "    err = str((result or {}).get('error') or '')\n"
        "    if 'Unknown action' in err:\n"
        "        unknown.append(cid + ': ' + err[:200])\n"
        "    if 'ModuleNotFoundError' in err or 'No module named' in err:\n"
        "        raise SystemExit(cid + ': ' + err)\n"
        "if unknown:\n"
        "    raise SystemExit('; '.join(unknown))\n"
        "if not calls:\n"
        "    raise SystemExit('schema-sample execute() was never reached')\n"
    )


def test_photographed_roster_and_factory_store_defaults():
    assert LIVE_VETCARE_REUSE_ACCEPT_CAPS == (
        "patient_records_management",
        "appointment_scheduling",
        "prescription_management",
        "billing_and_invoicing",
        "client_communication_portal",
    )
    assert default_block_action("validation") == "validate"
    assert default_block_action("analytics") == "track_event"
    assert default_block_action("team") == "create_team"
    assert default_block_action("database") == "query"
    assert default_block_action("event_bus") == EVENT_BUS_STEP_ACTION
    assert default_block_action("workflow") == "run"
    planted = default_action_from_block_json(
        {"inputs": [{"name": "action", "default": "check", "options": ["check"]}]}
    )
    assert planted == "check"
    assert default_block_action("validation", {"validation": "check"}) == "check"


def test_vetcare_compiled_brief_grounds_reuse_accept():
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    verify_inventory(compiled)
    text = compiled.text
    assert reuse_accept_acceptance_line() in text
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in text
    assert REUSE_ACCEPT_MISS in text
    assert "patient_records_management" in text
    for needle in reuse_accept_needles():
        assert needle.lower() in text.lower(), needle
    rules = reuse_accept_rules_text()
    assert PRODUCT_EVENT_BUS_STEP_0_HALT in rules
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    contract = reuse_accept_brief_contract()
    assert contract in CODING_AGENT_BRIEF
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in _WHOLE_JOB_SYSTEM
    assert "patient_records_management" in _WHOLE_JOB_SYSTEM


def test_empty_block_default_actions_is_reuse_accept_miss():
    body = _capability_handler_body(
        "patient_records_management", ["database", "validation"]
    )
    empty = _handler_module(
        "patient_records_management",
        ["database", "validation"],
        body,
        "factory-grounded persist",
        {},
        entity="patient_records_management",
    )
    assert parse_handler_default_actions(empty) == {}
    # Factory map still resolves these Store ids — the miss is an
    # unbound / unknown block, or an explicit action=None call.
    filled = apply_default_actions_to_handler(
        empty, harvest_block_default_actions(["database", "validation"])
    )
    assert parse_handler_default_actions(filled)["database"] == "query"
    assert parse_handler_default_actions(filled)["validation"] == "validate"
    assert reuse_accept_handler_errors(filled, ["database", "validation"]) == []
    ghost = apply_default_actions_to_handler(empty, {"database": "query"})
    errors = reuse_accept_handler_errors(
        ghost, ["database", "not_a_real_block"], capability_id="patient_records_management"
    )
    assert any(REUSE_ACCEPT_MISS in e for e in errors)
    assert any("not_a_real_block" in e for e in errors)


def test_emit_keep_path_populates_block_default_actions(tmp_path):
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    verify_inventory(compiled)
    _plant_store_block_json(tmp_path)
    emitted = emit_factory_grounded_reuse_keep_path(tmp_path, compiled)
    assert set(emitted) == set(LIVE_VETCARE_REUSE_ACCEPT_CAPS)
    for cid, bids in LIVE_VETCARE_REUSE_ACCEPT_BLOCKS.items():
        text = (tmp_path / "app" / "actions" / f"{cid}.py").read_text(encoding="utf-8")
        defaults = parse_handler_default_actions(text)
        assert defaults, cid
        for bid in bids:
            assert defaults.get(bid), (cid, bid, defaults)
            assert "action=BLOCK_DEFAULT_ACTIONS.get" in text or (
                f"action=BLOCK_DEFAULT_ACTIONS.get('{bid}')" in text
                or 'action=BLOCK_DEFAULT_ACTIONS.get("' in text
            )
        assert reuse_accept_handler_errors(text, bids, capability_id=cid) == []
    assert_reuse_schema_accept(tmp_path, compiled)
    sched = (tmp_path / "app" / "actions" / "appointment_scheduling.py").read_text(
        encoding="utf-8"
    )
    assert EVENT_BUS_STEP_ACTION in sched
    assert "execute(" in sched
    assert not re.search(r"execute\s*\([^)]*action\s*=\s*None", sched)


def test_empty_defaults_halt_before_tester(tmp_path):
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "patient_records_management.py").write_text(
        "CAPABILITY_ID = 'patient_records_management'\n"
        "BLOCK_IDS = ['not_a_real_block']\n"
        "BLOCK_DEFAULT_ACTIONS = {}\n"
        "def handle(payload):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    compiled.inventory = [
        item
        for item in compiled.inventory
        if item.capability_id == "patient_records_management"
    ]
    compiled.inventory[0].verified_present = ["not_a_real_block"]
    compiled.inventory[0].block_ids = ["not_a_real_block"]
    with pytest.raises(ReuseAcceptHalt, match=r"reuse_accept") as halted:
        assert_reuse_schema_accept(tmp_path, compiled)
    assert WRITER_REUSE_ACCEPT_HALT in str(halted.value)
    assert "not_a_real_block" in str(halted.value)


def test_writer_keep_path_schema_sample_has_no_unknown_action(
    tmp_path, monkeypatch
):
    """Live photograph roster: keep-path WRITER commit must keyword-dispatch."""
    _arm_billing_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: {"specs": {}, "handlers": {}, "model": "x"},
    )
    plan = _vetcare_reuse_plan()
    compiled = compile_brief(_VetCare(), plan, store_ids=STORE_IDS)
    assert all(item.is_reuse for item in compiled.inventory)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    dest.mkdir()
    _plant_store_block_json(dest)
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=staging)
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_VetCare(),
            plan=plan,
            state={
                "resolved_blocks": tuple(STORE_IDS),
                "vendored_blocks": tuple(STORE_IDS),
            },
        )
    )
    assert result.ok, result.detail
    receipt = json.loads(
        (staging / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["inventory_gaps"] == []
    ws.commit()
    for cid, bids in LIVE_VETCARE_REUSE_ACCEPT_BLOCKS.items():
        path = dest / "app" / "actions" / f"{cid}.py"
        assert path.is_file(), cid
        text = path.read_text(encoding="utf-8")
        defaults = parse_handler_default_actions(text)
        for bid in bids:
            assert defaults.get(bid), (cid, bid, defaults)
        assert "ModuleNotFoundError" not in text
        assert reuse_accept_handler_errors(text, bids, capability_id=cid) == []
    proc = subprocess.run(
        [sys.executable, "-c", _schema_sample_exec_script(LIVE_VETCARE_REUSE_ACCEPT_CAPS)],
        cwd=str(dest),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "pilot_zip" not in (result.detail or "").lower()
    assert "FACTORY_BRIEF_HTTP_ONESHOT" not in json.dumps(receipt)


def test_rendered_block_inputs_carries_store_default_map():
    from app.factory.build.block_inputs import render_block_inputs_module

    text = render_block_inputs_module()
    assert "def default_block_action" in text
    assert "STORE_BLOCK_DEFAULT_ACTIONS" in text
    assert "track_event" in text
    assert "create_team" in text
    assert "validate" in text
    assert repr(dict(STORE_BLOCK_DEFAULT_ACTIONS)) in text
