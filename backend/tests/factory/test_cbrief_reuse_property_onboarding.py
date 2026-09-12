"""C-BRIEF: property_onboarding / spec_analyzer must harvest a default action.

Measured sess_5782f2264e0e4ff4 Continue 2026-09-10 (estate-operations /
Cerebrum-Steward founding): WRITER stopped at [check:reuse_accept]:

    property_onboarding: spec_analyzer: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

Steward ``property_onboarding`` binds spec_analyzer +
recommendation_template + readiness_engine. Factory vendor
``spec_analyzer/block.json`` has no ``inputs[].name == action`` (adapter
``run()`` only). Same compiler class as #348 formula_executor / #351
vector_search / # capture — not a per-cap handle() micro-shot.

reuse_accept failed independently of the ~1490s phase wall: the CLI
session had already returned (~1488s) and keep-path emit had no
STORE_BLOCK_DEFAULT_ACTIONS entry for those ids. A 10s CLI would have
failed the same check.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.reuse_accept import (
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

LIVE_SESS = "sess_5782f2264e0e4ff4"
LIVE_ONBOARDING_CAP = "property_onboarding"
LIVE_ONBOARDING_BLOCKS = (
    "spec_analyzer",
    "recommendation_template",
    "readiness_engine",
)
LIVE_SPEC_ANALYZER_KEYWORD = "analyze"
LIVE_RECOMMENDATION_KEYWORD = "apply_template"
LIVE_READINESS_KEYWORD = "score"
LIVE_SPEC_ANALYZER_MISS = (
    "property_onboarding: spec_analyzer: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)
_FACTORY_SPEC_ANALYZER_PY = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "factory"
    / "vendor_blocks_mirror"
    / "spec_analyzer"
    / "block.py"
)
_REGISTRY_SHAPED_SPEC_ANALYZER = {
    "id": "spec_analyzer",
    "inputs": [
        {
            "description": "Property spec document or structured payload",
            "name": "input",
            "required": False,
            "type": "json",
        }
    ],
}


def test_sess_5782f2264e0e4ff4_photograph_and_vendor_harvest():
    """Live miss string + factory map harvest (no planted workspace)."""
    assert LIVE_SESS in __doc__
    assert "independently" in __doc__
    assert "spec_analyzer" in STORE_BLOCK_DEFAULT_ACTIONS
    assert STORE_BLOCK_DEFAULT_ACTIONS["spec_analyzer"] == LIVE_SPEC_ANALYZER_KEYWORD
    assert STORE_BLOCK_DEFAULT_ACTIONS["recommendation_template"] == (
        LIVE_RECOMMENDATION_KEYWORD
    )
    assert STORE_BLOCK_DEFAULT_ACTIONS["readiness_engine"] == LIVE_READINESS_KEYWORD
    assert default_block_action("spec_analyzer") == LIVE_SPEC_ANALYZER_KEYWORD
    assert default_block_action("recommendation_template") == LIVE_RECOMMENDATION_KEYWORD
    assert default_block_action("readiness_engine") == LIVE_READINESS_KEYWORD

    vendor = load_local_block_json("spec_analyzer")
    assert vendor is not None
    assert vendor.get("id") == "spec_analyzer"
    harvested_json = default_action_from_block_json(vendor)
    # Store pin has no action input; harvest still resolves via the factory map.
    assert harvested_json in {None, LIVE_SPEC_ANALYZER_KEYWORD}

    harvested = harvest_block_default_actions(list(LIVE_ONBOARDING_BLOCKS))
    assert harvested["spec_analyzer"] == LIVE_SPEC_ANALYZER_KEYWORD
    assert harvested["recommendation_template"] == LIVE_RECOMMENDATION_KEYWORD
    assert harvested["readiness_engine"] == LIVE_READINESS_KEYWORD
    assert harvest_block_default_action("spec_analyzer") == LIVE_SPEC_ANALYZER_KEYWORD
    assert default_block_action("not_a_real_block") is None

    ghost = reuse_accept_handler_errors(
        "BLOCK_IDS = ['spec_analyzer']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["spec_analyzer"],
        capability_id=LIVE_ONBOARDING_CAP,
    )
    # Factory map still resolves spec_analyzer — empty defaults are not
    # the live miss once the compiler harvests the Store default.
    assert ghost == []


def test_registry_shaped_spec_analyzer_misses_then_map_analyze():
    """Live vendor block.json has no action input; map fallback is analyze."""
    assert LIVE_SESS in __doc__
    assert default_action_from_block_json(_REGISTRY_SHAPED_SPEC_ANALYZER) is None
    adapter = _FACTORY_SPEC_ANALYZER_PY.read_text(encoding="utf-8")
    # Adapter run() is not action-dispatch; harvest-from-source misses.
    assert default_action_from_source(adapter) is None
    assert default_block_action("spec_analyzer") == LIVE_SPEC_ANALYZER_KEYWORD
    assert default_block_action("spec_analyzer", {}) == LIVE_SPEC_ANALYZER_KEYWORD
    errors = reuse_accept_handler_errors(
        "BLOCK_IDS = ['spec_analyzer']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["spec_analyzer"],
        capability_id=LIVE_ONBOARDING_CAP,
    )
    assert errors == []
    assert LIVE_SPEC_ANALYZER_MISS.split(":")[1].strip().startswith("spec_analyzer")


def test_property_onboarding_handler_accepts_schema_sample(tmp_path):
    """REUSE handler for property_onboarding must keyword-dispatch its blocks."""
    body = _capability_handler_body(LIVE_ONBOARDING_CAP, list(LIVE_ONBOARDING_BLOCKS))
    empty = _handler_module(
        LIVE_ONBOARDING_CAP,
        list(LIVE_ONBOARDING_BLOCKS),
        body,
        "factory-grounded persist",
        {},
        entity=LIVE_ONBOARDING_CAP,
    )
    filled = _handler_module(
        LIVE_ONBOARDING_CAP,
        list(LIVE_ONBOARDING_BLOCKS),
        body,
        "factory-grounded persist",
        harvest_block_default_actions(list(LIVE_ONBOARDING_BLOCKS)),
        entity=LIVE_ONBOARDING_CAP,
    )
    miss = reuse_accept_handler_errors(
        empty, list(LIVE_ONBOARDING_BLOCKS), capability_id=LIVE_ONBOARDING_CAP
    )
    # Empty BLOCK_DEFAULT_ACTIONS still resolves via the factory map.
    assert miss == []
    assert reuse_accept_handler_errors(
        filled, list(LIVE_ONBOARDING_BLOCKS), capability_id=LIVE_ONBOARDING_CAP
    ) == []
    ghost = reuse_accept_handler_errors(
        empty,
        ["spec_analyzer", "not_a_real_block"],
        capability_id=LIVE_ONBOARDING_CAP,
    )
    assert any(REUSE_ACCEPT_MISS in e and "not_a_real_block" in e for e in ghost)
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in LIVE_SPEC_ANALYZER_MISS


def test_path_and_role_workspace_harvest_spec_analyzer(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    vendor = dest / "vendor" / "blocks" / "spec_analyzer"
    vendor.mkdir(parents=True)
    (vendor / "block.json").write_text(
        json.dumps(
            {
                "id": "spec_analyzer",
                "inputs": [
                    {
                        "name": "action",
                        "type": "string",
                        "default": LIVE_SPEC_ANALYZER_KEYWORD,
                        "options": [LIVE_SPEC_ANALYZER_KEYWORD],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    assert harvest_block_default_action("spec_analyzer", dest) == LIVE_SPEC_ANALYZER_KEYWORD
    assert harvest_block_default_action("spec_analyzer", workspace=ws) == (
        LIVE_SPEC_ANALYZER_KEYWORD
    )
