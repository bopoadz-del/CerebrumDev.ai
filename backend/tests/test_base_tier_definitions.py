"""The base-tier definition set and its precedence rule.

The property under test throughout: **position is not authority.** A conflict
nobody declared stops the resolve rather than being decided by load order.

This module is a kernel-tier proposal, not a port of Product Delivery Standard
Section 15 (EXPORTS AND REPORTING), not an encoding-sheet engine, and not the
unimplemented AuthorityResolver / ConflictDetector named in
``docs/REASONING_KERNEL.md``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.cerebrum_product_kernel.formulas import (
    PrecedenceError,
    definition_index,
    load_base_definitions,
    resolve_definitions,
)

BACKEND = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[2]

#: A golden set: definitions that must exist at the base tier, with the tier
#: that must supply them. Not exhaustive -- it is the load-bearing subset whose
#: disappearance would change answers rather than merely shrink a catalogue.
GOLDEN_BASE = {
    "gross_margin_ratio": "base",
    "net_profit_margin": "base",
    "price_from_margin": "base",
    "break_even_units": "base",
    "net_from_gross": "base",
    "days_sales_outstanding": "base",
    "cash_conversion_cycle": "base",
    "straight_line_depreciation": "base",
    "compound_annual_growth_rate": "base",
}


def _overlay(**kwargs):
    """A minimal domain overlay."""
    base = {"set_id": "construction", "definitions": []}
    base.update(kwargs)
    return base


def _definition(ident, **kwargs):
    d = {
        "id": ident,
        "definition_version": 1,
        "name": ident,
        "category": "test",
        "expression": "a + b",
        "inputs": ["a", "b"],
        "output": "money",
    }
    d.update(kwargs)
    return d


# -- the base set ----------------------------------------------------------


def test_the_base_set_loads_and_is_all_base_tier():
    resolved = resolve_definitions()
    assert len(resolved) == 30, "29 definitions + 1 convention"
    assert {entry["tier"] for entry in resolved.values()} == {"base"}


def test_the_golden_definitions_are_present_at_the_base_tier():
    resolved = resolve_definitions()
    for ident, tier in GOLDEN_BASE.items():
        assert ident in resolved, f"{ident} vanished from the base set"
        assert resolved[ident]["tier"] == tier


def test_deleting_a_base_definition_is_caught():
    """The mutation test. A golden set that survives its own definitions being
    deleted is decoration; this proves the assertions above have teeth.

    It also pins the behaviour that matters at runtime: a missing definition
    must be ABSENT, so a caller asking for it gets nothing and can say so.
    The failure to avoid is a lookup that quietly falls through to an invented
    derivation, which reads identically to a grounded one.
    """
    mutated = copy.deepcopy(load_base_definitions())
    victim = "gross_margin_ratio"
    before = len(mutated["definitions"])
    mutated["definitions"] = [
        d for d in mutated["definitions"] if d["id"] != victim
    ]
    assert len(mutated["definitions"]) == before - 1, "the victim was not there to delete"

    resolved = resolve_definitions(base=mutated)

    assert victim not in resolved
    assert victim not in {row["id"] for row in definition_index(resolved)}
    # And the golden-set assertion must now fail, which is the point.
    with pytest.raises(AssertionError):
        for ident in GOLDEN_BASE:
            assert ident in resolved, f"{ident} vanished from the base set"


def test_addressing_example_names_a_real_base_definition():
    """The documented example address must exist in the set, or it is a lie."""
    base = load_base_definitions()
    example = base["addressing"]["example"]
    addresses = {
        "%s:%s_v%s" % (base["set_id"], d["id"], d["definition_version"])
        for d in base["definitions"] + base.get("conventions", [])
    }
    assert example in addresses, f"{example} is not a base address"


# -- extending -------------------------------------------------------------


def test_a_domain_may_add_what_the_base_does_not_have():
    resolved = resolve_definitions(
        overlays=[_overlay(definitions=[_definition("waste_factor")])]
    )
    assert resolved["waste_factor"]["tier"] == "domain-extension"
    assert resolved["waste_factor"]["origin"] == "construction"
    assert resolved["gross_margin_ratio"]["tier"] == "base", "base left intact"


# -- overriding ------------------------------------------------------------


def test_a_declared_override_wins_and_says_what_it_replaced():
    override = _definition(
        "gross_margin_ratio",
        expression="(revenue - cogs - rework) / revenue",
        inputs=["revenue", "cogs", "rework"],
        overrides="universal:gross_margin_ratio_v1",
        reason="Rework is not recoverable on fixed-price work and is treated as cost of sales.",
        provenance={"kind": "internal_protocol", "reference": "Contract schedule 4"},
    )
    resolved = resolve_definitions(overlays=[_overlay(definitions=[override])])

    entry = resolved["gross_margin_ratio"]
    assert entry["tier"] == "domain-override of base"
    assert entry.supersedes == "universal:gross_margin_ratio_v1"
    assert entry["origin"] == "construction"
    assert "rework" in entry["expression"]


def test_silent_shadowing_is_refused():
    """The failure this module exists to prevent."""
    shadow = _definition("gross_margin_ratio", expression="revenue / cogs")
    with pytest.raises(PrecedenceError, match="declare overrides"):
        resolve_definitions(overlays=[_overlay(definitions=[shadow])])


def test_an_override_of_a_revised_base_does_not_re_point_itself():
    """An override written against _v1 must not silently apply to _v2.

    This is why the version lives inside the address. Without it, revising a
    base definition would re-aim every override at arithmetic its author never
    reviewed, and nothing would say so.
    """
    revised = copy.deepcopy(load_base_definitions())
    for d in revised["definitions"]:
        if d["id"] == "gross_margin_ratio":
            d["definition_version"] = 2
            d["key"] = "universal:gross_margin_ratio_v2"

    stale = _definition(
        "gross_margin_ratio",
        overrides="universal:gross_margin_ratio_v1",
        reason="written against the old base",
        provenance={"kind": "internal_protocol", "reference": "x"},
    )
    with pytest.raises(PrecedenceError, match="no longer there"):
        resolve_definitions(base=revised, overlays=[_overlay(definitions=[stale])])


def test_an_override_must_carry_provenance_and_a_reason():
    no_prov = _definition(
        "gross_margin_ratio",
        overrides="universal:gross_margin_ratio_v1",
        reason="because",
    )
    with pytest.raises(PrecedenceError, match="provenance"):
        resolve_definitions(overlays=[_overlay(definitions=[no_prov])])

    no_reason = _definition(
        "gross_margin_ratio",
        overrides="universal:gross_margin_ratio_v1",
        provenance={"kind": "internal_protocol", "reference": "x"},
    )
    with pytest.raises(PrecedenceError, match="reason"):
        resolve_definitions(overlays=[_overlay(definitions=[no_reason])])


def test_an_override_must_name_the_definition_it_replaces():
    mismatched = _definition(
        "quick_ratio",
        overrides="universal:gross_margin_ratio_v1",
        reason="r",
        provenance={"kind": "internal_protocol", "reference": "x"},
    )
    with pytest.raises(PrecedenceError, match="belongs to"):
        resolve_definitions(overlays=[_overlay(definitions=[mismatched])])


def test_two_domains_cannot_both_claim_one_definition():
    a = _overlay(set_id="construction", definitions=[_definition("waste_factor")])
    b = _overlay(set_id="manufacturing", definitions=[_definition("waste_factor")])
    with pytest.raises(PrecedenceError, match="conflict to settle"):
        resolve_definitions(overlays=[a, b])


# -- attribution -----------------------------------------------------------


def test_the_index_states_the_tier_for_every_definition():
    resolved = resolve_definitions(
        overlays=[
            _overlay(
                definitions=[
                    _definition("waste_factor"),
                    _definition(
                        "quick_ratio",
                        overrides="universal:quick_ratio_v1",
                        reason="excludes retention receivables",
                        provenance={"kind": "internal_protocol", "reference": "x"},
                    ),
                ]
            )
        ]
    )
    index = {row["id"]: row for row in definition_index(resolved)}
    assert index["gross_margin_ratio"]["tier"] == "base"
    assert index["waste_factor"]["tier"] == "domain-extension"
    assert index["quick_ratio"]["tier"] == "domain-override of base"
    assert index["quick_ratio"]["supersedes"] == "universal:quick_ratio_v1"


# -- the path that actually ships -----------------------------------------


def test_formulas_are_not_routed_through_dead_tiers():
    """Lock the three dead routes so this set cannot drift back onto them."""
    shelf = json.loads(
        (BACKEND / "app" / "factory" / "shelves" / "factory_blocks.json").read_text(
            encoding="utf-8"
        )
    )
    kit_ids = {item.get("kit") for item in shelf.get("blocks", [])}
    block_ids = {item.get("id") for item in shelf.get("blocks", [])}
    assert "universal_business" not in kit_ids
    assert "universal_kernel" not in kit_ids
    assert "universal_business" not in block_ids
    assert "formula_executor_v2" not in block_ids

    kits_root = BACKEND / "app" / "factory" / "kits"
    assert not (kits_root / "universal_business").exists()
    assert not (kits_root / "universal_kernel").exists()
    assert not (kits_root / "formulas").exists()

    kernel_formulas = BACKEND / "app" / "cerebrum_product_kernel" / "formulas"
    assert (kernel_formulas / "universal_definitions.json").is_file()
    assert (kernel_formulas / "__init__.py").is_file()


def test_the_base_set_ships_into_a_product_via_generator_copytree(tmp_path, monkeypatch):
    """Replay the generator's own copytree, not a replica of its ignore list.

    ``ProductGenerator._write_app`` copytrees ``cerebrum_product_kernel`` into
    every generated product. Calling that method (not a hand-rolled copytree
    with the same ignore patterns) is what proves the files land on the tier
    that actually ships. A replica would stay green if the generator later
    started ignoring ``*.json``.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    from app.factory.blueprint import load_blueprint
    from app.factory.generator import ProductGenerator

    bp = load_blueprint(REPO / "blueprints" / "examples" / "basic_product.yaml")
    gen = ProductGenerator(
        bp, blocks_root=None, factory_commit="test", blocks_commit="test"
    )
    out = tmp_path / "product"
    out.mkdir()
    gen._write_app(out)

    formulas = out / "app" / "cerebrum_product_kernel" / "formulas"
    shipped_json = formulas / "universal_definitions.json"
    shipped_py = formulas / "__init__.py"
    assert shipped_json.is_file(), (
        "the base definitions did not reach the product; the tier is only "
        "'by construction' while it lives inside the kernel tree the "
        "generator copytrees"
    )
    assert shipped_py.is_file(), "the precedence module did not reach the product"
    src = shipped_py.read_text(encoding="utf-8")
    assert "def resolve_definitions" in src
    assert "class PrecedenceError" in src
    assert "Section 15" in src and "EXPORTS AND REPORTING" in src
    data = json.loads(shipped_json.read_text(encoding="utf-8"))
    assert len(data["definitions"]) == 29
    assert data["addressing"]["example"] == "universal:gross_margin_ratio_v1"

    kits = out / "kits"
    if kits.exists():
        assert not (kits / "universal_business").exists()
        assert not list(kits.rglob("universal_definitions.json"))


def test_the_base_set_ships_into_a_product_via_role_runner_vendor(tmp_path, monkeypatch):
    """RoleRunner vendors the kernel file-by-file, not via shutil.copytree.

    That is the other path that actually ships ``cerebrum_product_kernel`` into
    a generated product. If only the copytree were proven, a RoleRunner ignore
    of ``*.json`` would silently drop the set on the production default engine.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    smoke = REPO / "blueprints" / "examples" / "runner_smoke.yaml"
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(smoke), out).run()
    assert outcome.ok, outcome.to_dict()

    formulas = out / "app" / "cerebrum_product_kernel" / "formulas"
    assert (formulas / "universal_definitions.json").is_file()
    assert (formulas / "__init__.py").is_file()
    assert "def resolve_definitions" in (formulas / "__init__.py").read_text(
        encoding="utf-8"
    )
