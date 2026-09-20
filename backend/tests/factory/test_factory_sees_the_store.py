"""The Factory reads the Store's inventory instead of a copy of it.

What the Factory offered was a hand-written file: app/factory/shelves/
factory_blocks.json, 25 blocks with their kit assignments. The Store holds
136 blocks (every block_registry/<id>/block.json carries id, version and
trust_tier) and shelves 18 kits marked available. So the chat told a finance
customer there was no ready kit while block_store/kits/finance_ops sat there
marked available, carrying a chart-of-accounts governance block -- and the
coding agent wrote a five-entry chart of accounts from scratch.

Two questions were sharing one file. What the Store HOLDS is now read from
the Store. What the Factory has CLEARED to attach stays the Factory's own
statement, because nothing in the Store says which blocks are cleared for a
customer build -- all 136 are trust_tier "platform".
"""

from __future__ import annotations

import json

from app.factory.dual_registry import load_factory_shelf, shelf_from_store
from app.factory.kit_pack import kit_map_from_store, load_shelf_kit_map
from app.factory.store_catalog import store_catalog


def _store(tmp_path, blocks: dict, kits: dict):
    for bid, meta in blocks.items():
        d = tmp_path / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps(meta), encoding="utf-8")
    for kid, meta in kits.items():
        d = tmp_path / "block_store" / "kits" / kid
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


class TestTheShelfComesFromTheStore:
    def test_it_reads_id_version_and_trust_from_each_block(self, tmp_path):
        root = _store(
            tmp_path,
            {"alpha": {"id": "alpha", "version": "2.1.0", "trust_tier": "platform"}},
            {},
        )

        shelf = shelf_from_store(root)

        assert set(shelf) == {"alpha"}
        assert shelf["alpha"].version == "2.1.0"
        assert shelf["alpha"].trust_tier == "platform"
        assert shelf["alpha"].source == "cerebrum-blocks"

    def test_a_block_that_vouches_for_nothing_stays_unvouched(self, tmp_path):
        """compliance_gate refuses an empty tier; it is not ours to invent."""
        root = _store(tmp_path, {"alpha": {"id": "alpha", "version": "1"}}, {})

        assert shelf_from_store(root)["alpha"].trust_tier == ""

    def test_an_unreadable_store_yields_nothing_rather_than_guessing(self, tmp_path):
        assert shelf_from_store(tmp_path / "nowhere") == {}

    def test_the_real_store_is_larger_than_the_hand_written_shelf(self):
        assert len(shelf_from_store()) > len(load_factory_shelf())


class TestKitsComeFromTheStore:
    def test_only_kits_the_store_marks_available_are_read(self, tmp_path):
        root = _store(
            tmp_path,
            {},
            {
                "finance_ops": {"id": "finance_ops", "status": "available", "blocks": ["ledger"]},
                "half_built": {"id": "half_built", "status": "draft", "blocks": ["wip"]},
            },
        )

        mapping = kit_map_from_store(root)

        assert mapping == {"ledger": "finance_ops"}

    def test_the_template_kit_is_not_a_kit(self, tmp_path):
        root = _store(
            tmp_path, {}, {"_template": {"id": "_template", "status": "available", "blocks": ["x"]}}
        )

        assert kit_map_from_store(root) == {}


class TestClearanceIsStillTheFactorysStatement:
    def test_vendoring_is_unchanged_by_what_the_store_holds(self):
        """Kit FILES come from app/factory/kits/. Renaming a product's kit to
        one the Factory cannot vendor would ship an empty kits/."""
        assert set(load_shelf_kit_map().values()) <= {"platform", "private_estate_operations"}

    def test_the_catalog_separates_what_is_held_from_what_is_cleared(self):
        catalog = store_catalog()

        assert len(catalog["store_blocks"]) > len(catalog["blocks"]), (
            "the Store holds more than the Factory has cleared, and the chat "
            "must be able to say so"
        )
        assert catalog["store_kits"], "the Store's kits must be visible"
        assert set(catalog["blocks"]) <= set(catalog["store_blocks"])

    def test_nothing_uncleared_became_attachable(self):
        """The regression this nearly shipped: every connector in the Store
        reading as cleared, including the drives that are not."""
        catalog = store_catalog()

        assert catalog["not_cleared"], "uncleared parts must still be named"
        cleared_ids = {c["id"] for c in catalog["connectors"]}
        uncleared_ids = {c["id"] for c in catalog["not_cleared"]}
        assert not (cleared_ids & uncleared_ids)
