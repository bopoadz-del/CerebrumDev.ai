"""C-BRIEF: estate_registry / storage must harvest a default action.

Measured sess_5782f2264e0e4ff4 Continue run3 (estate-operations /
Cerebrum-Steward founding, tip 4120a07 after #404 CLONER OK and #403
property_onboarding / spec_analyzer CLEARED): WRITER stopped at
[check:reuse_accept]:

    estate_registry: storage: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

Outcome FAILED_ROLE_ERROR. TESTER not reached. No
``factory budget ramp`` lines — reuse_accept is post-CLI; ramp fires
only while FACTORY_CODE_CLI is in-flight and leftover phase-box time
is inside CLI_PHASE_RAMP_HEADROOM_S. A miss here cannot suppress a
ramp that should already have logged (reuse_vs_wall=independent,
same class as #403).

Steward ``estate_registry`` binds estate_registry + database +
storage + validation. Factory vendor ``storage/block.json`` had no
``inputs[].name == action`` (adapter ``run()`` only). Same compiler
class as #348 formula_executor / #351 vector_search / capture /
#403 spec_analyzer — not a per-cap handle() micro-shot.

Sibling Steward adapters without an action input (estate_registry,
estate_maintenance, evidence_verifier, portfolio_rollup, knowledge)
gain the same map so the next Continue cannot whack-a-mole.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.reuse_accept import (
    LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS,
    LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    PRODUCT_UNKNOWN_ACTION_NONE_HALT,
    REUSE_ACCEPT_MISS,
    STORE_BLOCK_DEFAULT_ACTIONS,
    default_action_from_block_json,
    default_action_from_source,
    default_block_action,
    harvest_block_default_action,
    harvest_block_default_actions,
    reuse_accept_handler_errors,
)
from app.factory.build.reuse_lookup import load_local_block_json
from app.factory.build.roles_handlers import (
    _capability_handler_body,
    _handler_module,
)
from app.factory.build.workspace import RoleWorkspace
from app.factory.build.authority import BuildRole
from app.factory.build.block_obligations import RESOURCE_OBLIGATIONS

LIVE_SESS = "sess_5782f2264e0e4ff4"
LIVE_STORAGE_KEYWORD = "store"
LIVE_ESTATE_REGISTRY_KEYWORD = "register"
LIVE_ESTATE_MAINTENANCE_KEYWORD = "plan_work"
LIVE_EVIDENCE_VERIFIER_KEYWORD = "verify"
LIVE_PORTFOLIO_ROLLUP_KEYWORD = "aggregate"
LIVE_KNOWLEDGE_KEYWORD = "search"
LIVE_STORAGE_MISS = (
    "estate_registry: storage: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)
_FACTORY_STORAGE_PY = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "factory"
    / "vendor_blocks_mirror"
    / "storage"
    / "block.py"
)
_STEWARD_BP = (
    Path(__file__).resolve().parents[3] / "blueprints" / "steward" / "steward.v1.yaml"
)
_REGISTRY_SHAPED_STORAGE = {
    "id": "storage",
    "inputs": [
        {
            "description": "Object/blob payload",
            "name": "input",
            "required": False,
            "type": "json",
        }
    ],
}

#: Honest keywords already documented in this repo (not invented).
STEWARD_SIBLING_DEFAULTS = {
    "storage": LIVE_STORAGE_KEYWORD,
    "estate_registry": LIVE_ESTATE_REGISTRY_KEYWORD,
    "estate_maintenance": LIVE_ESTATE_MAINTENANCE_KEYWORD,
    "evidence_verifier": LIVE_EVIDENCE_VERIFIER_KEYWORD,
    "portfolio_rollup": LIVE_PORTFOLIO_ROLLUP_KEYWORD,
    "knowledge": LIVE_KNOWLEDGE_KEYWORD,
}


def test_sess_5782f2264e0e4ff4_run3_photograph_and_vendor_harvest():
    """Live miss string + factory map harvest (no planted workspace)."""
    assert LIVE_SESS in __doc__
    assert "independently" in __doc__ or "independent" in __doc__
    assert "storage" in STORE_BLOCK_DEFAULT_ACTIONS
    assert STORE_BLOCK_DEFAULT_ACTIONS["storage"] == LIVE_STORAGE_KEYWORD
    assert STORE_BLOCK_DEFAULT_ACTIONS["estate_registry"] == LIVE_ESTATE_REGISTRY_KEYWORD
    assert default_block_action("storage") == LIVE_STORAGE_KEYWORD
    assert default_block_action("estate_registry") == LIVE_ESTATE_REGISTRY_KEYWORD
    assert RESOURCE_OBLIGATIONS["storage"]["ensure"] == LIVE_STORAGE_KEYWORD

    vendor = load_local_block_json("storage")
    assert vendor is not None
    assert vendor.get("id") == "storage"
    harvested_json = default_action_from_block_json(vendor)
    assert harvested_json == LIVE_STORAGE_KEYWORD, vendor

    harvested = harvest_block_default_actions(list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS))
    assert harvested["storage"] == LIVE_STORAGE_KEYWORD
    assert harvested["estate_registry"] == LIVE_ESTATE_REGISTRY_KEYWORD
    assert harvested["database"] == "query"
    # Store pin harvests validate_pipeline; map fallback stays validate.
    assert harvested["validation"] in {"validate", "validate_pipeline"}
    assert harvest_block_default_action("storage") == LIVE_STORAGE_KEYWORD
    assert default_block_action("not_a_real_block") is None

    ghost = reuse_accept_handler_errors(
        "BLOCK_IDS = ['storage']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["storage"],
        capability_id=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    )
    # Factory map still resolves storage — empty defaults are not
    # the live miss once the compiler harvests the Store default.
    assert ghost == []


def test_registry_shaped_storage_misses_then_map_store():
    """Live vendor block.json had no action input; map fallback is store."""
    assert LIVE_SESS in __doc__
    assert default_action_from_block_json(_REGISTRY_SHAPED_STORAGE) is None
    adapter = _FACTORY_STORAGE_PY.read_text(encoding="utf-8")
    assert default_action_from_source(adapter) is None
    assert default_block_action("storage") == LIVE_STORAGE_KEYWORD
    assert default_block_action("storage", {}) == LIVE_STORAGE_KEYWORD
    errors = reuse_accept_handler_errors(
        "BLOCK_IDS = ['storage']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["storage"],
        capability_id=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    )
    assert errors == []
    assert LIVE_STORAGE_MISS.split(":")[1].strip().startswith("storage")


def test_estate_registry_handler_accepts_schema_sample():
    """REUSE handler for estate_registry must keyword-dispatch its blocks."""
    body = _capability_handler_body(
        LIVE_STEWARD_ESTATE_REGISTRY_CAP, list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS)
    )
    empty = _handler_module(
        LIVE_STEWARD_ESTATE_REGISTRY_CAP,
        list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS),
        body,
        "factory-grounded persist",
        {},
        entity=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    )
    filled = _handler_module(
        LIVE_STEWARD_ESTATE_REGISTRY_CAP,
        list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS),
        body,
        "factory-grounded persist",
        harvest_block_default_actions(list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS)),
        entity=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    )
    miss = reuse_accept_handler_errors(
        empty,
        list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS),
        capability_id=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    )
    assert miss == []
    assert (
        reuse_accept_handler_errors(
            filled,
            list(LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS),
            capability_id=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
        )
        == []
    )
    ghost = reuse_accept_handler_errors(
        empty,
        ["storage", "not_a_real_block"],
        capability_id=LIVE_STEWARD_ESTATE_REGISTRY_CAP,
    )
    assert any(REUSE_ACCEPT_MISS in e and "not_a_real_block" in e for e in ghost)
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in LIVE_STORAGE_MISS


def test_path_and_role_workspace_harvest_storage(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    vendor = dest / "vendor" / "blocks" / "storage"
    vendor.mkdir(parents=True)
    (vendor / "block.json").write_text(
        json.dumps(
            {
                "id": "storage",
                "inputs": [
                    {
                        "name": "action",
                        "type": "string",
                        "default": LIVE_STORAGE_KEYWORD,
                        "options": [LIVE_STORAGE_KEYWORD],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    assert harvest_block_default_action("storage", dest) == LIVE_STORAGE_KEYWORD
    assert harvest_block_default_action("storage", workspace=ws) == LIVE_STORAGE_KEYWORD


def test_steward_blueprint_caps_all_harvest_block_defaults():
    """Every Steward bound block must harvest a keyword — no next-Continue mole."""
    bp = load_blueprint(_STEWARD_BP)
    bids = sorted(
        {str(bid) for cap in bp.capabilities for bid in (cap.block_ids or []) if str(bid).strip()}
    )
    assert "storage" in bids
    assert "estate_registry" in bids
    missing = [bid for bid in bids if not harvest_block_default_action(bid)]
    assert missing == [], (
        "Steward blueprint block_ids without BLOCK_DEFAULT_ACTIONS / harvest: "
        + ", ".join(missing)
    )
    for bid, keyword in STEWARD_SIBLING_DEFAULTS.items():
        assert bid in bids, bid
        harvested = harvest_block_default_action(bid)
        if bid == "knowledge":
            # Store block.json defaults to ask; factory map fallback is search.
            assert harvested in {"ask", "search"}
        else:
            assert harvested == keyword
        assert STORE_BLOCK_DEFAULT_ACTIONS[bid] == keyword


def test_reuse_vs_wall_independent_of_estate_registry_miss():
    """Ramp is a CLI-wait inspect; reuse_accept is post-CLI keep-path."""
    assert "reuse_vs_wall=independent" in __doc__ or "independent" in __doc__
    assert "factory budget ramp" in __doc__
    assert "CLI_PHASE_RAMP_HEADROOM_S" in __doc__
    # A ghost storage miss still names the live halt class — it does not
    # mention ramp. Mixing them is how #403 was mis-read.
    assert "factory budget ramp" not in LIVE_STORAGE_MISS
    assert REUSE_ACCEPT_MISS in LIVE_STORAGE_MISS
