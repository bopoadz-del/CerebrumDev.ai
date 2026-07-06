"""Tests for the Fork-class platform packager."""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from app.core.platform_packager import (
    MissingKitError,
    package_platform_session,
)
from app.models.session import SessionState


@pytest.fixture
def session(tmp_path: Path) -> SessionState:
    # Point storage away from the project tree.
    os.environ["STORAGE_PATH"] = str(tmp_path / "storage")
    return SessionState(
        session_id="sess_test_platform",
        user_id="user_1",
        config={"domain": "medical"},
        proposed_chain={"blocks": [{"id": "chat"}], "connections": []},
        chain_approved=True,
    )


@pytest.fixture
def fake_medical_kit(tmp_path: Path) -> Path:
    """Create a fake domain kit with a manifest and one artifact."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    kit_root = engine_root / "block_store" / "kits" / "medical"
    bundle = kit_root / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "app" / "containers").mkdir(parents=True)
    (bundle / "app" / "containers" / "medical.py").write_text(
        "class MedicalContainer:\n    name = 'medical'\n",
        encoding="utf-8",
    )
    manifest = {
        "id": "medical",
        "container": {"class": "app.containers.medical.MedicalContainer"},
        "blocks": ["chat"],
        "artifacts": [
            {"src": "app/containers/medical.py", "dest": "app/containers/medical.py"}
        ],
    }
    (kit_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Engine checkout must also contain the relocated CLI.
    cli_pkg = engine_root / "cli" / "cerebrum_cli"
    cli_pkg.mkdir(parents=True)
    (cli_pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    (cli_pkg / "marker_from_engine.txt").write_text("engine\n", encoding="utf-8")
    return kit_root


def test_package_platform_session_neutral(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """A platform package is generated and contains Fork-class files."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_medical_kit.parent.parent.parent))

    info = package_platform_session(session)

    assert info["service_name"].startswith("cerebrum-platform-medical-")
    assert Path(info["zip_path"]).exists()

    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "Dockerfile" in names
        assert "docker-compose.yml" in names
        assert "render.yaml" in names
        assert "entrypoint.sh" in names
        assert ".env" in names
        assert "default_chain.json" in names
        assert "vectors.json" in names
        assert "bootstrap.py" in names
        assert "README.platform.md" in names
        assert "app/containers/medical.py" in names

        # Domain-neutral: no construction-specific defaults leaked.
        env = zf.read(".env").decode()
        assert "CEREBRUM_DOMAIN_KITS=medical" in env
        assert "dar_al_arkan" not in env.lower()
        assert "construction" not in env.lower()

        render = zf.read("render.yaml").decode()
        assert "databases:" in render
        assert "cerebrum-platform-medical-" in render


def test_package_uses_fallback_kit_resolution(session: SessionState, tmp_path: Path):
    """Missing domain kit raises MissingKitError."""
    os.environ["STORAGE_PATH"] = str(tmp_path / "storage2")
    session.config.domain = "nonexistent_xyz"
    with pytest.raises(MissingKitError):
        package_platform_session(session)


def test_package_neutral_no_construction_strings(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """No construction-specific words leak into package text files."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_medical_kit.parent.parent.parent))

    info = package_platform_session(session)

    forbidden = ["the-fork", "dar_al_arkan", "projects_folder", "curated_kb"]
    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        for name in zf.namelist():
            if not name.endswith((".py", ".yml", ".yaml", ".json", ".md", ".sh", ".txt", ".env")):
                continue
            text = zf.read(name).decode(errors="ignore").lower()
            for word in forbidden:
                assert word not in text, f"forbidden word {word!r} found in {name}"


def test_package_cli_sourced_from_engine(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """The packaged CLI is copied from the engine checkout."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)

    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "cli/cerebrum_cli/__init__.py" in names
        assert "cli/cerebrum_cli/marker_from_engine.txt" in names
        assert zf.read("cli/cerebrum_cli/marker_from_engine.txt").decode().strip() == "engine"
        assert "cli/install.sh" in names
        assert "cli/config.toml" in names


def test_package_cli_config_stamps_deployed_mode(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """The packaged CLI config.toml declares mode = 'deployed'."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)

    config_path = Path(info["package_dir"]) / "cli" / "config.toml"
    assert config_path.exists()
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    assert cfg.get("mode") == "deployed"


def test_vectors_json_includes_embedding_meta(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """Platform package vectors.json always contains an embedding stanza."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    session.embedding_meta = {
        "provider": "zvec",
        "backend": "model2vec",
        "dimensions": 256,
        "model": "minishlab/potion-base-8M",
    }
    session.chunks = ["chunk one"]
    session.embeddings = [[0.1] * 256]

    info = package_platform_session(session)
    vectors = json.loads((Path(info["package_dir"]) / "vectors.json").read_text(encoding="utf-8"))
    assert vectors["embedding"] == {
        "provider": "zvec",
        "model": "minishlab/potion-base-8M",
        "dim": 256,
    }


def test_package_cli_missing_engine_cli_raises(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """A missing engine cli/ directory raises the mandated RuntimeError."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    # Remove the relocated CLI from the fake engine checkout.
    shutil.rmtree(engine_root / "cli")

    expected = (
        f"Engine checkout at {engine_root} lacks cli/ — requires Cerebrum-Blocks "
        "with the relocated CLI (Spec 2, commit c8176867 or later)"
    )
    with pytest.raises(RuntimeError, match=re.escape(expected)):
        package_platform_session(session)


def test_find_engine_root_falls_back_to_sibling_checkout(monkeypatch, tmp_path: Path):
    """When CEREBRUM_BLOCKS_ROOT is unset, the sibling Cerebrum-Blocks checkout is discovered."""
    from app.core.engine_discovery import _find_engine_root

    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    project_root = tmp_path / "CerebrumDev.ai"
    sibling = tmp_path / "Cerebrum-Blocks"
    sibling.mkdir()
    anchor = project_root / "backend" / "app" / "core" / "engine_discovery.py"
    assert _find_engine_root(anchor) == sibling
