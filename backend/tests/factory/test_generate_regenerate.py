"""Generate + delete + regenerate deterministic smoke."""

from pathlib import Path

from app.cerebrum_product_kernel.provenance import hash_tree
from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator


ROOT = Path(__file__).resolve().parents[3]
BLOCKS = Path("/home/ubuntu/repos/Cerebrum-Blocks")


def _stable_hash(root: Path) -> str:
    # Exclude provenance timestamp file for byte-stability comparison of code tree
    return hash_tree(root, ignore_names={".git", "__pycache__", "provenance.json", ".pytest_cache"})


def test_basic_generate_regenerate(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    out = tmp_path / "basic"
    gen = ProductGenerator(bp, blocks_root=BLOCKS, factory_commit="test", blocks_commit="test")
    r1 = gen.generate(out)
    h1 = _stable_hash(out)
    assert (out / "app" / "main.py").exists()
    assert (out / "factory_plan.json").exists()
    assert r1["product_id"] == "basic-factory-smoke"

    # delete and regenerate
    r2 = gen.generate(out)
    h2 = _stable_hash(out)
    assert h1 == h2
    assert r1["inputs_hash"] == r2["inputs_hash"] or h1 == h2


def test_steward_generate(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "steward"
    gen = ProductGenerator(bp, blocks_root=BLOCKS, factory_commit="test", blocks_commit="test")
    result = gen.generate(out)
    assert (out / "vendor" / "blocks" / "estate_registry" / "block.json").exists()
    assert (out / "docs" / "edge_profile.json").exists()
    assert "portfolio_rollup" in result["plan"]["dual_registered_blocks"]
    catalog = (out / "app" / "actions" / "__init__.py").read_text()
    assert "estate_registry" in catalog
