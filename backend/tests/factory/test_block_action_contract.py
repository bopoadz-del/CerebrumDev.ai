"""Blocks are action-dispatched, and the WRITER must be told their contract.

New-shape tests for the second failure of the first real build. With the
runtime slice vendored, every block imported -- and then rejected every
payload: handlers sent raw domain dicts to blocks that answer
``Unknown action: None`` or ``Input validation failed``, because nothing
carried the block's contract (default action, declared inputs, schema
required fields) from the vendored source to the code that calls it.

No LLM key and no network anywhere here: the one coder test stubs the wire
call and inspects the prompt it would have sent.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import (
    _DISPATCH_RUNTIME,
    RoleContext,
    _block_contract,
    _handler_module,
    _templated_body,
)
from app.factory.build.workspace import RoleWorkspace


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _platform_with_recording_block(tmp_path: Path) -> Path:
    """A minimal built-platform layout whose one block records its kwargs."""
    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(_DISPATCH_RUNTIME, encoding="utf-8")
    block = root / "vendor" / "blocks" / "recorder"
    block.mkdir(parents=True)
    (block / "block.py").write_text(
        "CALLS = []\n"
        "def run(**kwargs):\n"
        "    CALLS.append(kwargs)\n"
        "    return {'status': 'ok', 'seen': sorted(kwargs)}\n",
        encoding="utf-8",
    )
    return root


def test_dispatch_forwards_action_and_params(tmp_path):
    """The first build could not even express an action: execute() had no
    parameter for it, so every real block answered "Unknown action: None"."""
    root = _platform_with_recording_block(tmp_path)
    dispatch = _load("dispatch_action_probe", root / "app" / "dispatch.py")

    out = dispatch.execute(
        "recorder", {"name": "x"}, action="create_team", params={"limit": 3}
    )
    assert out["status"] == "ok"
    block = dispatch.load_block("recorder")
    assert block.CALLS[-1] == {
        "input": {"name": "x"},
        "action": "create_team",
        "limit": 3,
    }


def test_dispatch_without_action_stays_bare(tmp_path):
    """No action means none is sent -- inventing one would silently change
    behaviour for blocks that dispatch on its absence."""
    root = _platform_with_recording_block(tmp_path)
    dispatch = _load("dispatch_bare_probe", root / "app" / "dispatch.py")

    dispatch.execute("recorder", {"name": "x"})
    block = dispatch.load_block("recorder")
    assert block.CALLS[-1] == {"input": {"name": "x"}}


def test_the_templated_handler_sends_each_blocks_default_action(tmp_path):
    """The deterministic path must at least reach a real action -- the
    block.json default -- instead of the guaranteed 'Unknown action: None'."""
    module_text = _handler_module(
        "crew_assignment",
        ["team"],
        _templated_body(["team"]),
        "deterministic contract template",
        {"team": "create_team"},
    )
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []

    # The handler does ``from app.dispatch import execute``; inject a fake
    # module under that name for the duration of the exec.
    import sys
    import types

    fake = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        calls.append((block_id, action))
        return {"status": "ok"}

    fake.execute = _fake_execute
    sys.modules["app.dispatch"] = fake
    try:
        spec = importlib.util.spec_from_file_location("handler_probe", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.handle({"crew": "C1"})
    finally:
        del sys.modules["app.dispatch"]

    assert calls == [("team", "create_team")]


def _workspace_with_contract(tmp_path: Path) -> RoleContext:
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER, workspace=ws, blueprint=None, plan=None
    )
    ws.write_text(
        Path("vendor") / "blocks" / "team" / "block.json",
        json.dumps(
            {
                "id": "team",
                "inputs": [
                    {"name": "input", "type": "json", "required": False},
                    {
                        "name": "action",
                        "type": "string",
                        "default": "create_team",
                        "options": ["create_team", "invite_member"],
                    },
                    {"name": "max_members_per_team", "type": "number"},
                ],
            }
        ),
    )
    ws.write_text(
        Path("vendor") / "cerebrum" / "blocks" / "team.py",
        textwrap.dedent(
            '''
            from vendor.cerebrum.core.typed_block import TypedBlock, Schema, ContentType

            class TeamBlock(TypedBlock):
                input_schema = Schema(
                    content_type=ContentType.JSON,
                    required_fields=["name", "members"],
                    optional_fields=["org"],
                )
            '''
        ),
    )
    return ctx


def test_block_contract_reads_json_and_schema(tmp_path):
    """default action + options from block.json; required input fields from
    the vendored module's Schema. Both existed on disk during the failed
    build -- nothing read them."""
    ctx = _workspace_with_contract(tmp_path)
    contract = _block_contract(ctx, "team")

    assert contract["default_action"] == "create_team"
    assert contract["action_options"] == ["create_team", "invite_member"]
    assert contract["input_required_fields"] == ["name", "members"]
    names = [item["name"] for item in contract["declared_inputs"]]
    assert "max_members_per_team" in names
    assert "action" not in names


def test_block_contract_survives_a_bare_block(tmp_path):
    """A mirror stub has no block.json worth reading and no runtime module.
    The contract degrades to just the id instead of raising."""
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(role=BuildRole.CLONER, workspace=ws, blueprint=None, plan=None)
    ws.write_text(
        Path("vendor") / "blocks" / "solo" / "block.py", "def run(**k):\n    return {}\n"
    )
    assert _block_contract(ctx, "solo") == {"block_id": "solo"}


def test_the_coder_prompt_carries_the_block_contract(monkeypatch):
    """The agent cannot honour a contract it was never shown. The prompt must
    contain the actions and required fields the handler has to satisfy."""
    from app.factory import coder

    captured = {}

    def _capture(messages):
        captured["messages"] = messages
        return 'return {"ok": True, "capability": CAPABILITY_ID}'

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr(coder, "_llm_code_call", _capture)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {"model": "stub"},
    )

    coder.generate_platform_handler(
        capability_id="crew_assignment",
        description="assign a crew",
        block_ids=["team"],
        product_name="Field Ops",
        vertical="field_operations",
        block_contracts={
            "team": {
                "block_id": "team",
                "default_action": "create_team",
                "input_required_fields": ["name", "members"],
            }
        },
    )

    user_message = captured["messages"][-1]["content"]
    assert "create_team" in user_message
    assert "input_required_fields" in user_message
    assert "members" in user_message
