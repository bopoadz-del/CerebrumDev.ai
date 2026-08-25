"""The base-tier definition set and its precedence rule.

The property under test throughout: **position is not authority.** Every other
overlay mechanism in this codebase settles a collision by who ran last (the
platform renderer overwrites silently) or who ran first (kit install skips).
Here the winning layer is stated in the data, and a conflict nobody declared
stops the resolve rather than being decided by load order.
"""

from __future__ import annotations

import copy

import pytest

from app.cerebrum_product_kernel.formulas import (
    PrecedenceError,
    definition_index,
    load_base_definitions,
    resolve_definitions,
)

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


def test_the_base_set_ships_into_a_product_by_construction(tmp_path):
    """No manifest, no declaration -- it arrives because the kernel arrives.

    ``generator._write_app`` copytrees the whole kernel package into every
    generated product, ignoring only ``__pycache__`` and ``*.pyc``. This
    replays that exact call and asserts the definitions land, so the tier
    cannot be quietly severed from the product by a change to the ignore
    patterns or by the file being moved out of the kernel tree.
    """
    import shutil
    from pathlib import Path

    import app.cerebrum_product_kernel as kernel

    kernel_src = Path(kernel.__file__).parent
    dest = tmp_path / "cerebrum_product_kernel"
    shutil.copytree(
        kernel_src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )

    shipped = dest / "formulas" / "universal_definitions.json"
    assert shipped.is_file(), (
        "the base definitions did not reach the product; the tier is only "
        "'by construction' while it lives inside the kernel tree"
    )
    import json

    assert len(json.loads(shipped.read_text(encoding="utf-8"))["definitions"]) == 29
