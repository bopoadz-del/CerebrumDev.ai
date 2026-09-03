"""factory_repo_root resolves local + Docker layouts."""

from __future__ import annotations


from app.factory.paths import factory_repo_root
from app.factory.product_architect import lettings_golden_path, steward_golden_path


def test_factory_repo_root_finds_blueprints():
    root = factory_repo_root()
    assert (root / "blueprints").is_dir()
    assert steward_golden_path().is_file()
    assert lettings_golden_path().is_file()


def test_factory_repo_root_docker_layout(tmp_path, monkeypatch):
    app_pkg = tmp_path / "app" / "factory"
    app_pkg.mkdir(parents=True)
    (tmp_path / "blueprints" / "steward").mkdir(parents=True)
    (tmp_path / "blueprints" / "steward" / "steward.v1.yaml").write_text("x: 1\n")
    anchor = app_pkg / "paths.py"
    anchor.write_text("# anchor\n")
    assert factory_repo_root(anchor) == tmp_path
