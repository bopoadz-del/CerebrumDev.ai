"""Product Architect — drafts product_blueprint.v1 inside the Factory.

Today Steward may be predefined (golden YAML). The architect can:
- load a checked-in blueprint (deterministic / mock / golden path)
- or draft from a brief using the LLM when configured

Architecture is always a validated ProductBlueprint; generation stays fail-closed
via the capability planner + dual registry.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.factory.blueprint import ProductBlueprint, load_blueprint
from app.factory.dual_registry import DualRegistryError, dual_registered_ids
from app.factory.generator import ProductGenerator, git_head
from app.factory.paths import factory_repo_root
from app.factory.planner import CapabilityPlanner, ProductPlan


def _repo_root() -> Path:
    return factory_repo_root()


def steward_golden_path() -> Path:
    return _repo_root() / "blueprints" / "steward" / "steward.v1.yaml"


def draft_blueprint_from_brief(
    brief: str,
    *,
    vertical_hint: Optional[str] = None,
    use_golden_steward: bool = True,
) -> ProductBlueprint:
    """Draft a ProductBlueprint from a user brief.

    For the Steward vertical (or when ``use_golden_steward`` and the brief
    mentions steward/estate), return the checked-in golden blueprint so the
    agent path stays regenerate-deterministic. Otherwise build a minimal
    blueprint using only dual-registered blocks discovered from the brief.
    """
    text = (brief or "").lower()
    wants_steward = any(
        k in text for k in ("steward", "estate", "private estate", "property readiness")
    )
    if use_golden_steward and (wants_steward or vertical_hint == "estate"):
        return load_blueprint(steward_golden_path())

    dual = sorted(dual_registered_ids())
    # Pick blocks mentioned in the brief; fall back to audit if none
    mentioned = [b for b in dual if b.replace("_", " ") in text or b in text]
    if not mentioned and "audit" in dual:
        mentioned = ["audit"]

    vertical = (vertical_hint or "product").replace(" ", "_").lower()
    product_id = re.sub(r"[^a-z0-9-]+", "-", vertical)[:48].strip("-") or "product"
    caps = []
    if mentioned:
        for bid in mentioned[:8]:
            caps.append(
                {
                    "id": bid,
                    "description": f"Capability backed by dual-registered block {bid}",
                    "block_ids": [bid],
                    "strategy_hint": "REUSE",
                }
            )
    else:
        caps.append(
            {
                "id": "health_surface",
                "description": "Generated health and capability surface",
                "block_ids": [],
                "strategy_hint": "GENERATE",
            }
        )

    raw = {
        "schema_version": "product_blueprint.v1",
        "product_id": product_id,
        "product_name": product_id.replace("-", " ").title(),
        "vertical": vertical,
        "summary": brief.strip()[:500] or f"Factory-drafted {vertical} product",
        "capabilities": caps,
        "ui_modules": ["command_center"],
        "connectors": [],
        "edge_profile": "standard",
        "human_authority": True,
    }
    return ProductBlueprint.model_validate(raw)


def plan_blueprint(
    blueprint: ProductBlueprint,
    *,
    blocks_root: Optional[Path] = None,
) -> ProductPlan:
    return CapabilityPlanner(blocks_root).plan(blueprint)


def generate_product(
    blueprint: ProductBlueprint,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    factory_root = _repo_root()
    blocks = Path(blocks_root) if blocks_root else None
    if blocks is None:
        env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
        if env:
            blocks = Path(env)
    gen = ProductGenerator(
        blueprint,
        blocks_root=blocks,
        factory_commit=git_head(factory_root),
        blocks_commit=git_head(blocks) if blocks else "unknown",
    )
    return gen.generate(output_dir)


def architect_pipeline(
    brief: str,
    output_dir: Path | str,
    *,
    vertical_hint: Optional[str] = None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Brief → blueprint → plan → generate. Fail closed on UNSUPPORTED."""
    try:
        bp = draft_blueprint_from_brief(brief, vertical_hint=vertical_hint)
        plan = plan_blueprint(bp, blocks_root=blocks_root)
        result = generate_product(bp, output_dir, blocks_root=blocks_root)
        return {
            "ok": True,
            "blueprint": bp.model_dump(mode="json"),
            "plan": plan.to_dict(),
            "generation": {
                "output_dir": result["output_dir"],
                "inputs_hash": result["inputs_hash"],
                "product_id": result["product_id"],
            },
        }
    except DualRegistryError as exc:
        return {"ok": False, "error": str(exc)}


def blueprint_to_yaml(bp: ProductBlueprint) -> str:
    return yaml.safe_dump(bp.model_dump(mode="json"), sort_keys=False)
