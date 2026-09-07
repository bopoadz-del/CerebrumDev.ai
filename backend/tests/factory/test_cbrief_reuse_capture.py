"""C-BRIEF: REUSE keep-path must harvest capture default action.

Photographed Floor after tip 3b9261b (sess_e8e4ab66e6dd4765,
estate-operations / Private Estate Steward Platform): WRITER stopped
at [check:reuse_accept]:

    maintenance_and_work_order_management: capture: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)
    security_and_access_logging: capture: reuse/accept miss —

Registry-verified Cerebrum-Blocks capture/block.json has no
inputs[].name == action (Store CaptureBlock.process defaults
params.action to capture). Factory vendor mirror and the documented
Store map both omitted that harvest. Same compiler class as
#348 formula_executor / #351 vector_search — not a per-cap handle()
micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.reuse_accept import (
    PRODUCT_UNKNOWN_ACTION_NONE_HALT,
    REUSE_ACCEPT_MISS,
    STORE_BLOCK_DEFAULT_ACTIONS,
    WRITER_REUSE_ACCEPT_HALT,
    ReuseAcceptHalt,
    assert_reuse_schema_accept,
    default_action_from_block_json,
    default_action_from_source,
    default_block_action,
    harvest_block_default_action,
    harvest_block_default_actions,
    reuse_accept_handler_errors,
)
from app.factory.build.reuse_lookup import load_local_block_json
from app.factory.build.workspace import RoleWorkspace

LIVE_SESS = "sess_e8e4ab66e6dd4765"
LIVE_CAPTURE_CAPS = (
    "maintenance_and_work_order_management",
    "security_and_access_logging",
)
LIVE_CAPTURE_MISS = (
    "maintenance_and_work_order_management: capture: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)
_REGISTRY_SHAPED_CAPTURE = {
    "id": "capture",
    "inputs": [
        {
            "description": "Upload or push an image to capture and structure...",
            "name": "input",
            "required": False,
            "type": "file",
        }
    ],
}


def test_sess_e8e4ab66e6dd4765_photograph_and_vendor_harvest():
    """Live miss string + factory vendor block.json harvest (no planted workspace)."""
    assert LIVE_SESS in __doc__
    assert "capture" in STORE_BLOCK_DEFAULT_ACTIONS
    assert STORE_BLOCK_DEFAULT_ACTIONS["capture"] == "capture"
    assert STORE_BLOCK_DEFAULT_ACTIONS["capture_v2"] == "capture"
    assert default_block_action("capture") == "capture"
    assert default_block_action("capture_v2") == "capture"

    vendor = load_local_block_json("capture")
    assert vendor is not None
    assert vendor.get("id") == "capture"
    harvested_json = default_action_from_block_json(vendor)
    assert harvested_json == "capture", vendor

    harvested = harvest_block_default_actions(["capture", "capture_v2", "database"])
    assert harvested["capture"] == "capture"
    assert harvested["capture_v2"] == "capture"
    assert harvested["database"] == "query"
    assert harvest_block_default_action("capture") == "capture"
    assert harvest_block_default_action("capture_v2") == "capture"
    assert default_block_action("not_a_real_block") is None
    assert harvest_block_default_actions(["not_a_real_block"]) == {}

    ghost = reuse_accept_handler_errors(
        "BLOCK_IDS = ['capture']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["capture"],
        capability_id="maintenance_and_work_order_management",
    )
    # Factory map still resolves capture — empty defaults are not
    # the live miss once the compiler harvests the Store default.
    assert ghost == []


def test_path_and_role_workspace_harvest_capture(tmp_path):
    """Path / RoleWorkspace harvest capture from planted vendor block.json."""
    dest = tmp_path / "dest"
    dest.mkdir()
    planted = dest / "vendor" / "blocks" / "capture"
    planted.mkdir(parents=True)
    (planted / "block.json").write_text(
        json.dumps(
            {
                "id": "capture",
                "inputs": [
                    {
                        "name": "action",
                        "type": "string",
                        "default": "capture",
                        "options": ["capture", "ocr"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert harvest_block_default_action("capture", dest) == "capture"
    assert harvest_block_default_actions(["capture"], dest) == {"capture": "capture"}

    ws = RoleWorkspace(BuildRole.WRITER, dest)
    assert harvest_block_default_action("capture", workspace=ws) == "capture"
    assert harvest_block_default_actions(["capture"], workspace=ws) == {
        "capture": "capture"
    }
    assert harvest_block_default_action("capture", workspace=tmp_path) == "capture"


def test_mutation_drops_capture_harvest(monkeypatch, tmp_path):
    """sess_e8e4ab66e6dd4765: dropping harvest still fails as Unknown action: None."""
    assert harvest_block_default_action("capture") == "capture"

    monkeypatch.setattr(
        "app.factory.build.reuse_accept.STORE_BLOCK_DEFAULT_ACTIONS",
        {
            key: value
            for key, value in STORE_BLOCK_DEFAULT_ACTIONS.items()
            if key not in {"capture", "capture_v2"}
        },
    )
    monkeypatch.setattr(
        "app.factory.build.reuse_accept._harvest_from_factory_vendor",
        lambda _bid: None,
    )
    dest = tmp_path / "vendor" / "blocks" / "capture"
    dest.mkdir(parents=True)
    (dest / "block.json").write_text(
        json.dumps(_REGISTRY_SHAPED_CAPTURE),
        encoding="utf-8",
    )
    (dest / "block.py").write_text(
        "def run(**kwargs):\n    return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    assert harvest_block_default_action("capture", tmp_path) is None
    assert harvest_block_default_actions(["capture"], tmp_path) == {}

    errors = reuse_accept_handler_errors(
        "BLOCK_IDS = ['capture']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["capture"],
        capability_id="maintenance_and_work_order_management",
    )
    assert LIVE_CAPTURE_MISS in errors
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in LIVE_CAPTURE_MISS
    assert REUSE_ACCEPT_MISS in LIVE_CAPTURE_MISS

    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "maintenance_and_work_order_management.py").write_text(
        "CAPABILITY_ID = 'maintenance_and_work_order_management'\n"
        "BLOCK_IDS = ['capture']\n"
        "BLOCK_DEFAULT_ACTIONS = {}\n"
        "def handle(payload):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    class _Item:
        capability_id = "maintenance_and_work_order_management"
        is_gap = False
        is_reuse = True
        handler_source = "reuse"
        verified_present = ["capture"]
        block_ids = ["capture"]

    with pytest.raises(ReuseAcceptHalt, match=r"reuse_accept") as halted:
        assert_reuse_schema_accept(tmp_path, [_Item()])
    assert WRITER_REUSE_ACCEPT_HALT in str(halted.value)
    assert LIVE_CAPTURE_MISS in str(halted.value)
    assert LIVE_SESS in __doc__
    assert LIVE_CAPTURE_CAPS[1] == "security_and_access_logging"


def test_registry_shaped_block_json_without_action_falls_to_factory_vendor():
    """Cerebrum-Blocks capture/block.json has no action input.

    Harvest must still fill capture from factory vendor / Store map rather
    than inventing an unknown id.
    """
    assert default_action_from_block_json(_REGISTRY_SHAPED_CAPTURE) is None
    store_source = (
        "async def process(self, input_data, params=None):\n"
        "    params = params or {}\n"
        '    action = params.get("action", "capture")\n'
        '    if action == "capture":\n'
        "        return await self._capture(input_data, params)\n"
        "    return {'status': 'error', 'error': f'Unknown action: {action}'}\n"
    )
    assert default_action_from_source(store_source) == "capture"
    assert harvest_block_default_action("capture") == "capture"
    assert harvest_block_default_action("not_a_real_block") is None
