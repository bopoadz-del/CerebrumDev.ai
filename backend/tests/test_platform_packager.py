"""Tests for the Fork-class platform packager."""

from __future__ import annotations

import json
import os
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
    kit_root = tmp_path / "Cerebrum-Blocks" / "block_store" / "kits" / "medical"
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
