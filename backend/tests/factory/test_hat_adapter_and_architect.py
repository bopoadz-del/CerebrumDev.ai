"""Hat adaptation (TEK patterns) + Product Architect pipeline."""

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator
from app.factory.hat_adapter import build_hat_manifests, build_workflows
from app.factory.planner import CapabilityPlanner
from app.factory.product_architect import architect_pipeline, draft_blueprint_from_brief


ROOT = Path(__file__).resolve().parents[3]
BLOCKS = Path("/home/ubuntu/repos/Cerebrum-Blocks")


def test_steward_hats_adapted_from_tek_pattern():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    plan = CapabilityPlanner(BLOCKS).plan(bp)
    hats = build_hat_manifests(bp, plan)
    kinds = {h["kind"] for h in hats}
    assert "base" in kinds and "hat" in kinds
    base = next(h for h in hats if h["kind"] == "base")
    assert base["agent_id"] == "estate.base"
    assert base["extends"] is None
    assert any(h["agent_id"].startswith("estate.hat.") for h in hats)
    assert "retail." not in base["agent_id"]


def test_steward_workflows_composed():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    plan = CapabilityPlanner(BLOCKS).plan(bp)
    workflows = build_workflows(bp, plan)
    ids = {w["workflow_id"] for w in workflows}
    assert "estate.ops_loop" in ids
    assert "estate.portfolio_rollup" in ids


def test_generator_emits_hats_and_workflows(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "steward"
    ProductGenerator(bp, blocks_root=BLOCKS, factory_commit="t", blocks_commit="t").generate(out)
    assert (out / "app" / "agents" / "manifests").exists()
    assert list((out / "app" / "agents" / "manifests").glob("*.json"))
    assert (out / "app" / "workflows" / "workflows.json").exists()
    assert (out / "frontend" / "src" / "modules").exists()


def test_architect_brief_uses_steward_golden(tmp_path, monkeypatch):
    # Agent manifests / hats are a TEMPLATE-path artifact; production now
    # builds through the role runner, which does not emit them (registered
    # in KNOWN_INCOMPLETE). Pin the engine so this keeps guarding the
    # template contract it was written for.
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
    bp = draft_blueprint_from_brief("Build Cerebrum Steward for private estate ops")
    assert bp.product_id == "cerebrum-steward"
    result = architect_pipeline(
        "Generate The Steward private estate platform",
        tmp_path / "out",
        blocks_root=BLOCKS,
    )
    assert result["ok"] is True
    assert result["generation"]["product_id"] == "cerebrum-steward"
    assert (Path(result["generation"]["output_dir"]) / "app" / "agents" / "manifests").exists()
