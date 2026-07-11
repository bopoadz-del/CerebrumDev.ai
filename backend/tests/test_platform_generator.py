"""Tests for the Fork-derived platform generator."""

import json
import shutil
from pathlib import Path

import pytest

from app.models.platform_manifest import parse_manifest
from app.platform_generator import PlatformGenerator
from app.platform_generator.fork_baseline import FORK_BASELINE_COMMIT


@pytest.fixture
def manifest_path() -> Path:
    return (
        Path(__file__).parent.parent.parent
        / "docs"
        / "superpowers"
        / "fixtures"
        / "automotive_platform_manifest.json"
    )


@pytest.fixture
def manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _make_fake_fork(tmp_path: Path, commit: str) -> Path:
    """Create a minimal fake Fork checkout at the given commit."""
    import subprocess

    fake_fork = tmp_path / "fake-fork"
    fake_fork.mkdir()
    (fake_fork / "app").mkdir()
    (fake_fork / "app" / "main.py").write_text(
        'print("The Fork backend")', encoding="utf-8"
    )
    (fake_fork / "alembic").mkdir()
    (fake_fork / "alembic" / "README").write_text("migrations", encoding="utf-8")
    (fake_fork / "frontend" / "src").mkdir(parents=True)
    (fake_fork / "frontend" / "src" / "App.tsx").write_text(
        'export default function App() { return <div>The Fork Construction Workspace</div> }',
        encoding="utf-8",
    )
    (fake_fork / "Dockerfile").write_text("FROM python:3.11", encoding="utf-8")
    (fake_fork / "render.yaml").write_text("services: []", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=fake_fork, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=fake_fork, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "pinned"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )
    # Reset to the exact pinned commit if one is supplied; tests that bypass
    # the pin check pass a placeholder and skip this step.
    if commit != "dummy":
        subprocess.run(
            ["git", "reset", "--hard", commit],
            cwd=fake_fork,
            check=True,
            capture_output=True,
        )
    return fake_fork


def test_generator_from_dict(manifest: dict, tmp_path: Path) -> None:
    from app.platform_generator.fork_baseline import FORK_BASELINE_COMMIT

    fork_path = _make_fake_fork(tmp_path, "dummy")
    output = tmp_path / "generated-automotive"
    generator = PlatformGenerator.from_dict(
        manifest,
        fork_path=fork_path,
        blocks_path=tmp_path / "dummy-blocks",
    )
    # Bypass the commit-pin check; the fake repo cannot match the real SHA.
    generator._verify_fork_baseline = lambda: FORK_BASELINE_COMMIT
    inputs_hash = generator.generate(output)

    assert output.exists()
    assert (output / "app" / "main.py").exists()
    assert (output / "alembic").is_dir()
    assert (output / "frontend" / "src" / "App.tsx").exists()
    assert (output / "platform_manifest.json").exists()
    assert (output / ".generation_inputs_hash").exists()

    # The hash file should start with the SHA-256 digest.
    hash_text = (output / ".generation_inputs_hash").read_text(encoding="utf-8")
    assert hash_text.splitlines()[0] == inputs_hash.hash

    # The manifest inside the package should be valid.
    embedded = json.loads((output / "platform_manifest.json").read_text(encoding="utf-8"))
    parsed = parse_manifest(embedded)
    assert parsed.platform_id == "automotive-safety-intelligence"

    # Branding substitution should have happened.
    app_tsx = (output / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "The Fork" not in app_tsx
    assert "Construction Workspace" not in app_tsx
    assert parsed.branding.workspace_name in app_tsx


def test_generator_refuses_wrong_fork_commit(manifest: dict, tmp_path: Path) -> None:
    """Generator must refuse a Fork checkout that does not match the pinned commit."""
    fake_fork = tmp_path / "fake-fork"
    fake_fork.mkdir()
    (fake_fork / "app").mkdir()
    (fake_fork / "alembic").mkdir()
    (fake_fork / "frontend").mkdir()
    (fake_fork / "Dockerfile").write_text("FROM python:3.11", encoding="utf-8")
    (fake_fork / "render.yaml").write_text("services: []", encoding="utf-8")

    # Initialise git and create a commit that is NOT the pinned baseline.
    import subprocess

    subprocess.run(["git", "init"], cwd=fake_fork, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=fake_fork, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=fake_fork,
        check=True,
        capture_output=True,
    )

    generator = PlatformGenerator.from_dict(
        manifest,
        fork_path=fake_fork,
        blocks_path=tmp_path / "dummy-blocks",
    )
    with pytest.raises(RuntimeError, match="does not match pinned baseline"):
        generator.generate(tmp_path / "out")


def test_generator_refuses_existing_output(manifest: dict, tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    generator = PlatformGenerator.from_dict(
        manifest,
        fork_path=tmp_path / "fake-fork",
        blocks_path=tmp_path / "dummy-blocks",
    )
    with pytest.raises(RuntimeError, match="Output directory already exists"):
        generator.generate(existing)


def test_text_filter_replaces_branding(manifest: dict, tmp_path: Path) -> None:
    from app.platform_generator.template_filters import make_text_filter

    parsed = parse_manifest(manifest)
    text_filter = make_text_filter(parsed)

    sample = "Welcome to The Fork Construction Workspace."
    transformed = text_filter(sample)
    assert "The Fork" not in transformed
    assert "Construction Workspace" not in transformed
    assert parsed.branding.product_name in transformed
    assert parsed.branding.workspace_name in transformed
