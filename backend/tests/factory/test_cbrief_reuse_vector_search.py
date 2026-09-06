"""C-BRIEF: REUSE keep-path must harvest vector_search default action.

Photographed Floor after #350 (tip 467c83e, sess_8259e197749b4441,
VetClinic Hub / veterinary-care, all-REUSE): appointment workflow
``result`` key did not recur. Export refuse PASS; pilot_zip=no.
WRITER stopped at [check:reuse_accept]:

    patient_records_management: vector_search: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

#348 harvested formula_executor from factory vendor_blocks_mirror /
CEREBRUM_BLOCKS_ROOT block.json (including Store _v2 alias). Registry
vector_search/block.json has no inputs[].name == action (Store runtime
defaults params.operation to search). Factory vendor mirror and the
documented Store map both omitted that harvest. Same compiler class —
not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json

import pytest

from app.factory.build.brief_compiler import compile_brief, verify_inventory
from app.factory.build.brief_lint import lint_brief
from app.factory.build.coder_session import emit_factory_grounded_reuse_keep_path
from app.factory.build.reuse_accept import (
    LIVE_VETCARE_REUSE_ACCEPT_BLOCKS,
    LIVE_VETCARE_REUSE_ACCEPT_CAPS,
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
    parse_handler_default_actions,
    reuse_accept_handler_errors,
    reuse_accept_needles,
)
from app.factory.build.reuse_lookup import load_local_block_json
from tests.factory.test_cbrief_reuse_schema_accept import (
    STORE_IDS,
    _VetCare,
    _vetcare_reuse_plan,
)

LIVE_SESS = "sess_8259e197749b4441"
LIVE_VECTOR_SEARCH_MISS = (
    "patient_records_management: vector_search: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)


def test_sess_8259e197749b4441_photograph_and_vendor_harvest():
    """Live miss string + factory vendor block.json harvest (no planted workspace)."""
    assert LIVE_SESS in __doc__
    assert LIVE_VETCARE_REUSE_ACCEPT_CAPS[0] == "patient_records_management"
    assert LIVE_VETCARE_REUSE_ACCEPT_BLOCKS["patient_records_management"] == [
        "database",
        "validation",
        "vector_search",
    ]
    assert "vector_search" in STORE_BLOCK_DEFAULT_ACTIONS
    assert STORE_BLOCK_DEFAULT_ACTIONS["vector_search"] == "search"
    assert default_block_action("vector_search") == "search"

    vendor = load_local_block_json("vector_search")
    assert vendor is not None
    assert vendor.get("id") == "vector_search"
    harvested_json = default_action_from_block_json(vendor)
    assert harvested_json == "search", vendor

    harvested = harvest_block_default_actions(
        ["vector_search", "database", "validation"]
    )
    assert harvested["vector_search"] == "search"
    assert harvested["database"] == "query"
    assert harvested["validation"] == "validate"
    assert harvest_block_default_action("vector_search") == "search"
    assert default_block_action("not_a_real_block") is None
    assert harvest_block_default_actions(["not_a_real_block"]) == {}

    ghost = reuse_accept_handler_errors(
        "BLOCK_IDS = ['vector_search']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["vector_search"],
        capability_id="patient_records_management",
    )
    # Factory map still resolves vector_search — empty defaults are not
    # the live miss once the compiler harvests the Store default.
    assert ghost == []


def test_emit_keep_path_vector_search_without_planted_vendor(tmp_path):
    """Keep-path emit must set BLOCK_DEFAULT_ACTIONS['vector_search'] from vendor."""
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    verify_inventory(compiled)
    assert "vector_search" in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    for needle in reuse_accept_needles():
        assert needle.lower() in compiled.text.lower(), needle

    assert not (tmp_path / "vendor" / "blocks" / "vector_search").exists()
    emitted = emit_factory_grounded_reuse_keep_path(tmp_path, compiled)
    assert set(emitted) == set(LIVE_VETCARE_REUSE_ACCEPT_CAPS)
    text = (tmp_path / "app" / "actions" / "patient_records_management.py").read_text(
        encoding="utf-8"
    )
    defaults = parse_handler_default_actions(text)
    assert defaults.get("vector_search") == "search", defaults
    assert defaults.get("vector_search") is not None
    assert reuse_accept_handler_errors(
        text,
        LIVE_VETCARE_REUSE_ACCEPT_BLOCKS["patient_records_management"],
        capability_id="patient_records_management",
    ) == []
    assert_reuse_schema_accept(tmp_path, compiled)


def test_mutation_drops_vector_search_harvest(monkeypatch, tmp_path):
    """sess_8259e197749b4441: dropping harvest still fails as Unknown action: None."""
    assert harvest_block_default_action("vector_search") == "search"

    monkeypatch.setattr(
        "app.factory.build.reuse_accept.STORE_BLOCK_DEFAULT_ACTIONS",
        {
            key: value
            for key, value in STORE_BLOCK_DEFAULT_ACTIONS.items()
            if key != "vector_search"
        },
    )
    monkeypatch.setattr(
        "app.factory.build.reuse_accept._harvest_from_factory_vendor",
        lambda _bid: None,
    )
    dest = tmp_path / "vendor" / "blocks" / "vector_search"
    dest.mkdir(parents=True)
    (dest / "block.json").write_text(
        json.dumps({"id": "vector_search"}),
        encoding="utf-8",
    )
    (dest / "block.py").write_text(
        "def run(**kwargs):\n    return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    assert harvest_block_default_action("vector_search", tmp_path) is None
    assert harvest_block_default_actions(["vector_search"], tmp_path) == {}

    errors = reuse_accept_handler_errors(
        "BLOCK_IDS = ['vector_search']\nBLOCK_DEFAULT_ACTIONS = {}\n",
        ["vector_search"],
        capability_id="patient_records_management",
    )
    assert LIVE_VECTOR_SEARCH_MISS in errors
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in LIVE_VECTOR_SEARCH_MISS
    assert REUSE_ACCEPT_MISS in LIVE_VECTOR_SEARCH_MISS

    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "patient_records_management.py").write_text(
        "CAPABILITY_ID = 'patient_records_management'\n"
        "BLOCK_IDS = ['vector_search']\n"
        "BLOCK_DEFAULT_ACTIONS = {}\n"
        "def handle(payload):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    compiled.inventory = [
        item
        for item in compiled.inventory
        if item.capability_id == "patient_records_management"
    ]
    compiled.inventory[0].verified_present = ["vector_search"]
    compiled.inventory[0].block_ids = ["vector_search"]
    with pytest.raises(ReuseAcceptHalt, match=r"reuse_accept") as halted:
        assert_reuse_schema_accept(tmp_path, compiled)
    assert WRITER_REUSE_ACCEPT_HALT in str(halted.value)
    assert LIVE_VECTOR_SEARCH_MISS in str(halted.value)
    assert LIVE_SESS in __doc__


def test_registry_shaped_block_json_without_action_falls_to_factory_vendor():
    """Cerebrum-Blocks vector_search/block.json has only an ``input`` field.

    Harvest must still fill search from factory vendor / Store map rather
    than inventing an unknown id.
    """
    registry_shaped = {
        "id": "vector_search",
        "inputs": [
            {
                "description": "Search knowledge base...",
                "name": "input",
                "required": False,
                "type": "string",
            }
        ],
    }
    assert default_action_from_block_json(registry_shaped) is None
    store_source = (
        "async def process(self, input_data, params=None):\n"
        "    params = params or {}\n"
        '    operation = params.get("operation", "search")\n'
        "    return operation\n"
    )
    assert default_action_from_source(store_source) == "search"
    assert harvest_block_default_action("vector_search") == "search"
    assert harvest_block_default_action("not_a_real_block") is None
