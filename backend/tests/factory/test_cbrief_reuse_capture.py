"""C-BRIEF: REUSE keep-path must harvest capture default action.

Photographed Floor after tip 3b9261b (sess_e8e4ab66e6dd4765,
estate-operations / Private Estate Steward Platform): WRITER stopped
at [check:reuse_accept]:

    maintenance_and_work_order_management: capture: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)
    security_and_access_logging: capture: reuse/accept miss —

Live InsureDistribute Store-green zip (sess_d10dfc28):
app/actions/capture.py sets BLOCK_DEFAULT_ACTIONS = {'capture': 'extract'}.
vendor/blocks/capture/block.json has id capture but inputs[] are OCR
config only (no name==action / operation). Factory vendor block.py is
an adapter (get_block + execute) with no action == dispatch, so
harvest-from-source misses. Factory-known map fallback is capture →
extract (capture_v2 alias). Same compiler class as #348 / #351 —
not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path

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
LIVE_INSURE_SESS = "sess_d10dfc28"
LIVE_CAPTURE_KEYWORD = "extract"
LIVE_CAPTURE_CAPS = (
    "maintenance_and_work_order_management",
    "security_and_access_logging",
)
LIVE_CAPTURE_MISS = (
    "maintenance_and_work_order_management: capture: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)
_FACTORY_CAPTURE_PY = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "factory"
    / "vendor_blocks_mirror"
    / "capture"
    / "block.py"
)
#: Live Store vendor / InsureDistribute zip: OCR config only, no action input.
_REGISTRY_SHAPED_CAPTURE = {
    "id": "capture",
    "inputs": [
        {
            "description": "Upload or push an image to capture and structure...",
            "name": "input",
            "required": False,
            "type": "file",
        },
        {
            "default": "tesseract",
            "name": "ocr_engine",
            "required": False,
            "type": "string",
        },
    ],
}


def test_sess_e8e4ab66e6dd4765_photograph_and_vendor_harvest():
    """Live miss string + factory vendor / map harvest (no planted workspace)."""
    assert LIVE_SESS in __doc__
    assert LIVE_INSURE_SESS in __doc__
    assert "capture" in STORE_BLOCK_DEFAULT_ACTIONS
    assert STORE_BLOCK_DEFAULT_ACTIONS["capture"] == LIVE_CAPTURE_KEYWORD
    assert STORE_BLOCK_DEFAULT_ACTIONS["capture_v2"] == LIVE_CAPTURE_KEYWORD
    assert default_block_action("capture") == LIVE_CAPTURE_KEYWORD
    assert default_block_action("capture_v2") == LIVE_CAPTURE_KEYWORD

    vendor = load_local_block_json("capture")
    assert vendor is not None
    assert vendor.get("id") == "capture"
    harvested_json = default_action_from_block_json(vendor)
    assert harvested_json == LIVE_CAPTURE_KEYWORD, vendor

    harvested = harvest_block_default_actions(["capture", "capture_v2", "database"])
    assert harvested["capture"] == LIVE_CAPTURE_KEYWORD
    assert harvested["capture_v2"] == LIVE_CAPTURE_KEYWORD
    assert harvested["database"] == "query"
    assert harvest_block_default_action("capture") == LIVE_CAPTURE_KEYWORD
    assert harvest_block_default_action("capture_v2") == LIVE_CAPTURE_KEYWORD
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


def test_insure_store_green_zip_registry_json_misses_then_map_extract():
    """sess_d10dfc28: live vendor block.json has no action/operation input.

    Harvest-from-block.json returns None. In-repo factory adapter
    block.py has no action == dispatch. Map fallback is extract.
    """
    assert LIVE_INSURE_SESS in __doc__
    assert default_action_from_block_json(_REGISTRY_SHAPED_CAPTURE) is None
    adapter = _FACTORY_CAPTURE_PY.read_text(encoding="utf-8")
    assert "get_block" in adapter
    assert default_action_from_source(adapter) is None
    assert "action ==" not in adapter
    assert default_block_action("capture") == "extract"
    assert default_block_action("capture", {}) == "extract"
    assert reuse_accept_handler_errors(
        "BLOCK_IDS = ['capture']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["capture"],
        capability_id="capture",
    ) == []
    assert reuse_accept_handler_errors(
        "BLOCK_IDS = ['capture']\nBLOCK_DEFAULT_ACTIONS = {'capture': 'extract'}\n",
        ["capture"],
        capability_id="capture",
    ) == []


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
                        "default": "extract",
                        "options": ["extract", "ocr"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert harvest_block_default_action("capture", dest) == "extract"
    assert harvest_block_default_actions(["capture"], dest) == {"capture": "extract"}

    ws = RoleWorkspace(BuildRole.WRITER, dest)
    assert harvest_block_default_action("capture", workspace=ws) == "extract"
    assert harvest_block_default_actions(["capture"], workspace=ws) == {
        "capture": "extract"
    }
    assert harvest_block_default_action("capture", workspace=tmp_path) == "extract"


def test_registry_shaped_workspace_falls_to_map_extract(monkeypatch, tmp_path):
    """Live zip layout: workspace vendor json has no action; map fills extract."""
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
        _FACTORY_CAPTURE_PY.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert default_action_from_block_json(_REGISTRY_SHAPED_CAPTURE) is None
    assert default_action_from_source((dest / "block.py").read_text()) is None
    assert harvest_block_default_action("capture", tmp_path) == "extract"
    assert harvest_block_default_actions(["capture"], tmp_path) == {
        "capture": "extract"
    }


def test_mutation_drops_capture_harvest(monkeypatch, tmp_path):
    """sess_e8e4ab66e6dd4765: dropping harvest still fails as Unknown action: None."""
    assert harvest_block_default_action("capture") == "extract"

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
    """Live Store / registry capture/block.json has no action input.

    Harvest-from-block.json misses. In-repo adapter source also misses.
    Factory vendor / Store map must fill extract rather than inventing
    an unknown id.
    """
    assert default_action_from_block_json(_REGISTRY_SHAPED_CAPTURE) is None
    assert default_action_from_source(_FACTORY_CAPTURE_PY.read_text()) is None
    assert harvest_block_default_action("capture") == "extract"
    assert harvest_block_default_action("not_a_real_block") is None
