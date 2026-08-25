"""Converge RoleRunner onto ProductGenerator class emitters.

U10: two emitters. ProductGenerator already writes the 14-class contract.
role_runner dropped eight of those classes. Converge invokes the existing
ProductGenerator methods into a scratch directory and copies the result
through the WRITER workspace so authority still judges every write.

Must not call ``ProductGenerator.generate()`` — that ``rmtree``s the
destination and would overwrite the S4 kernel already vendored here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, Tuple

from app.factory.blueprint import ProductBlueprint, blueprint_to_dict
from app.factory.planner import ProductPlan

#: The 14-class contract from ProductGenerator README / generator.py:229-247.
FOURTEEN_ARTIFACT_CLASSES: Tuple[str, ...] = (
    "app/main.py",
    "app/actions",
    "app/agents/manifests",
    "app/workflows",
    "app/cerebrum_product_kernel",
    "app/connectors",
    "product-dna",
    "docs/blueprint",
    "docs/provenance",
    "docs/certification",
    "frontend",
    "vendor/blocks",
    "kits",
    "scripts/release_gate.py",
)

#: Trees WRITER copies from ProductGenerator emitters. vendor/** and kits/**
#: stay CLONER's lane (and vendor is sealed after CLONER).
CONVERGED_TREES: Tuple[str, ...] = (
    "app/agents",
    "app/workflows",
    "app/connectors",
    "product-dna",
    "docs/blueprint",
    "docs/certification",
    "frontend",
)

CONVERGED_FILES: Tuple[str, ...] = ("docs/edge_profile.json",)

#: Honest extras ProductGenerator.generate() still writes that RoleRunner
#: does not. Declared, not silently dropped, not required for parity.
DECLARED_GENERATOR_EXTRAS: Tuple[str, ...] = (
    "product-agent/",
    "factory_plan.json",
    "pyproject.toml",
    "app/static/console.html",
    "resident-engineer docs / inject_resident_runtime",
)

DECLARED_RUNNER_EXTRAS: Tuple[str, ...] = (
    "app/dispatch.py",
    "app/kernel_bridge.py",
    "app/domain_ops.py",
    "app/work_queue.py",
    "docs/build_provenance.json",
    "docs/domain_acceptance.json",
    "docs/domain_pack.json",
)


def present_classes(root: Path) -> Dict[str, bool]:
    """Which of the 14 classes exist under *root* (file or directory)."""
    base = Path(root)
    return {rel: (base / rel).exists() for rel in FOURTEEN_ARTIFACT_CLASSES}


def missing_classes(root: Path) -> Tuple[str, ...]:
    return tuple(rel for rel, ok in present_classes(root).items() if not ok)


def converge_writer_emitters(ctx: Any) -> Dict[str, Any]:
    """Emit the eight dropped classes via ProductGenerator methods.

    No-ops on unit-test stubs that are not a real ``ProductBlueprint`` /
    ``ProductPlan`` so ratchet tests keep a narrow workspace.
    """
    blueprint = getattr(ctx, "blueprint", None)
    plan = getattr(ctx, "plan", None)
    if not isinstance(blueprint, ProductBlueprint):
        return {"ok": False, "skipped": "blueprint is not ProductBlueprint"}
    if not isinstance(plan, ProductPlan):
        return {"ok": False, "skipped": "plan is not ProductPlan"}

    from app.cerebrum_product_kernel.provenance import build_provenance
    from app.factory.generator import ProductGenerator
    from app.product_dna.emit import emit_product_dna

    factory_commit = str(ctx.state.get("factory_commit") or "unknown")
    blocks_commit = str(ctx.state.get("blocks_commit") or "unknown")
    gen = ProductGenerator(
        blueprint,
        plan=plan,
        blocks_root=getattr(ctx, "blocks_root", None),
        factory_commit=factory_commit,
        blocks_commit=blocks_commit,
    )

    copied: list[str] = []
    with TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        agents = gen._write_hats(scratch)
        workflows = gen._write_workflows(scratch)
        gen._write_connectors(scratch)
        gen._write_ui_stub(scratch)
        gen._write_blueprint_copy(scratch)
        gen._write_edge_profile(scratch)
        gen._write_certification_scaffold(scratch)
        actions = [
            {
                "capability_id": cap.capability_id,
                "strategy": cap.strategy,
                "block_ids": list(cap.block_ids or []),
            }
            for cap in plan.capabilities
        ]
        emit_product_dna(
            scratch,
            blueprint,
            plan,
            factory_commit=factory_commit,
            blocks_commit=blocks_commit,
            actions=actions,
            agents=agents,
            workflows=workflows,
        )
        workspace = ctx.workspace
        for rel in CONVERGED_TREES:
            src = scratch / rel
            if src.is_dir():
                workspace.copy_tree(src, rel)
                copied.append(rel)
        for rel in CONVERGED_FILES:
            src = scratch / rel
            if src.is_file():
                workspace.copy_file(src, rel)
                copied.append(rel)

    payload = json.dumps(
        blueprint_to_dict(blueprint), sort_keys=True, separators=(",", ":")
    )
    inputs_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    prov = build_provenance(
        product_id=blueprint.product_id,
        blueprint_id=f"{blueprint.product_id}:{blueprint.schema_version}",
        factory_commit=factory_commit,
        blocks_commit=blocks_commit,
        plan=plan.to_dict(),
        inputs_hash=inputs_hash,
    )
    # ProductGenerator stamps wall-clock generated_at. RoleRunner cannot:
    # two identical builds must byte-match, and coder variance must stay
    # inside app/actions/. The field remains; the value is the input hash.
    prov["generated_at"] = f"blueprint:{inputs_hash}"
    ctx.workspace.write_text(
        Path("docs") / "provenance" / "provenance.json",
        json.dumps(prov, indent=2, sort_keys=True) + "\n",
    )
    copied.append("docs/provenance/provenance.json")
    return {"ok": True, "copied": copied, "skipped": ""}
