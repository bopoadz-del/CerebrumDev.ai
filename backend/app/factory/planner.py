"""Capability planner — resolves REUSE/ADAPT/COMPOSE/GENERATE/STUB/UNSUPPORTED."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from app.factory.blueprint import CapabilitySpec, CapabilityStrategyHint, ProductBlueprint
from app.factory.dual_registry import DualRegistryError, assert_dual_registered, dual_registered_ids


@dataclass
class PlannedCapability:
    capability_id: str
    strategy: str
    block_ids: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ProductPlan:
    product_id: str
    capabilities: List[PlannedCapability]
    unsupported: List[str] = field(default_factory=list)
    dual_registered_blocks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capabilities": [asdict(c) for c in self.capabilities],
            "dual_registered_blocks": list(self.dual_registered_blocks),
            "product_id": self.product_id,
            "unsupported": list(self.unsupported),
        }


class CapabilityPlanner:
    """Fail-closed planner: unknown or non-dual-registered blocks → UNSUPPORTED."""

    def __init__(self, blocks_root=None, factory_shelf=None):
        self.blocks_root = blocks_root
        self.factory_shelf = factory_shelf
        self._dual = dual_registered_ids(blocks_root, factory_shelf)

    def plan(self, blueprint: ProductBlueprint, *, fail_on_unsupported: bool = True) -> ProductPlan:
        planned: List[PlannedCapability] = []
        unsupported: List[str] = []
        used_blocks: List[str] = []

        for cap in blueprint.capabilities:
            item = self._plan_one(cap)
            planned.append(item)
            used_blocks.extend(item.block_ids)
            if item.strategy == CapabilityStrategyHint.UNSUPPORTED.value:
                unsupported.append(cap.id)

        if fail_on_unsupported and unsupported:
            raise DualRegistryError(
                "UNSUPPORTED capabilities (fail closed): " + ", ".join(unsupported)
            )

        # Final dual gate for every block referenced
        if used_blocks:
            assert_dual_registered(used_blocks, self.blocks_root, self.factory_shelf)

        return ProductPlan(
            product_id=blueprint.product_id,
            capabilities=planned,
            unsupported=unsupported,
            dual_registered_blocks=sorted(set(used_blocks)),
        )

    def _plan_one(self, cap: CapabilitySpec) -> PlannedCapability:
        hint = cap.strategy_hint
        if hint == CapabilityStrategyHint.UNSUPPORTED:
            return PlannedCapability(cap.id, "UNSUPPORTED", [], "explicit UNSUPPORTED hint")

        if not cap.block_ids:
            strategy = (hint or CapabilityStrategyHint.GENERATE).value
            if strategy == "UNSUPPORTED":
                return PlannedCapability(cap.id, "UNSUPPORTED", [], "no blocks and unsupported")
            return PlannedCapability(
                cap.id,
                strategy if strategy in {"GENERATE", "STUB", "ADAPT"} else "GENERATE",
                [],
                "kernel/template generation (no block ids)",
            )

        missing = [b for b in cap.block_ids if b not in self._dual]
        if missing:
            return PlannedCapability(
                cap.id,
                "UNSUPPORTED",
                [],
                f"blocks not dual-registered: {', '.join(missing)}",
            )

        if hint:
            strategy = hint.value
        elif len(cap.block_ids) > 1:
            strategy = "COMPOSE"
        else:
            strategy = "REUSE"

        if strategy == "UNSUPPORTED":
            return PlannedCapability(cap.id, "UNSUPPORTED", [], "hint UNSUPPORTED")

        return PlannedCapability(cap.id, strategy, list(cap.block_ids), "dual-registered")
