"""A delivered platform must be deployable, not merely runnable locally.

New-shape tests for the deploy scaffold. The runner produced a working
application with no way to ship it: an inventory against the template
generator showed Dockerfile, Procfile, render.yaml and .env.example among the
83 files only the old path emitted. Without them the artifact runs on a
developer's machine and nowhere else.

The scaffold is templated rather than coder-written on purpose. Container and
process config is mechanical — there is no domain judgement for an agent to
add, and a hallucinated base image or start command is a deployment failure
rather than a test failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import AuthorityError, BuildRole, assert_write_allowed
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def built(tmp_path):
    out = tmp_path / "build"
    assert RoleRunner(load_blueprint(SMOKE), out).run().ok
    return out


def test_the_deploy_scaffold_is_present(built):
    for name in ("Dockerfile", ".dockerignore", "Procfile", ".env.example", "render.yaml"):
        assert (built / name).is_file(), f"missing {name}"


def test_the_dockerfile_starts_the_platform_and_provisions_storage(built):
    text = (built / "Dockerfile").read_text(encoding="utf-8")
    assert "uvicorn" in text and "app.main:app" in text
    assert "requirements.txt" in text
    # sqlite needs its directory to exist; the container must create it.
    assert "STORAGE_PATH" in text
    assert "mkdir -p" in text
    # F19: a red suite must not produce a deployable image.
    assert "scripts/release_gate.py" in text
    assert "requirements-dev.txt" in text


def test_the_render_blueprint_declares_no_database(built):
    """Persistence is a sqlite file. A Postgres or key-value block here would
    provision paid infrastructure the platform never uses -- the exact class of
    charge that prompted rebuilding this deployment in the first place."""
    text = (built / "render.yaml").read_text(encoding="utf-8")
    assert "type: web" in text
    assert "healthCheckPath: /health" in text
    assert "mountPath: /app/data" in text
    for forbidden in ("type: pserv", "postgres", "keyvalue", "redis"):
        assert forbidden not in text.lower(), forbidden


def test_the_service_name_is_slugged_from_the_product_id(tmp_path):
    out = tmp_path / "b"
    assert RoleRunner(load_blueprint(SMOKE), out).run().ok
    text = (out / "render.yaml").read_text(encoding="utf-8")
    # product_id is "runner-smoke"; a raw value with spaces or capitals would
    # be rejected by Render.
    assert "name: runner-smoke" in text
    assert "name: runner-smoke-data" in text


def test_the_env_example_offers_no_store_wiring(built):
    """The template generator's .env.example documents a store URL because its
    handlers POST to one. This platform's handlers import the blocks vendored
    beside them, so offering a store variable would misdescribe how it runs."""
    text = (built / ".env.example").read_text(encoding="utf-8")
    assert "STORAGE_PATH" in text
    assert "P1" in text
    for token in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "/v1/execute"):
        assert f"{token}=" not in text, f"{token} offered in a P1 platform"


def test_the_dockerignore_excludes_build_and_runtime_artefacts(built):
    text = (built / ".dockerignore").read_text(encoding="utf-8")
    for entry in ("__pycache__/", "data/", ".env", "build_ledger.jsonl"):
        assert entry in text, entry


def test_the_writer_lane_admits_the_scaffold_but_stays_narrow(tmp_path):
    """Named files, not a root wildcard: the writer must not be able to drop
    arbitrary files at the workspace root."""
    ws = tmp_path / "w"
    ws.mkdir()
    for allowed in ("Dockerfile", "Procfile", ".env.example", ".dockerignore", "render.yaml"):
        assert assert_write_allowed(BuildRole.WRITER, ws / allowed, workspace=ws)
    for allowed in (
        "product-dna/entity_model.json",
        "docs/blueprint/product_blueprint.json",
        "docs/provenance/provenance.json",
        "docs/certification/dual_certification.json",
        "docs/edge_profile.json",
        "docs/network_posture.json",
        "frontend/src/App.tsx",
    ):
        assert assert_write_allowed(BuildRole.WRITER, ws / allowed, workspace=ws)
    for denied in ("docker-compose.yml", "Makefile", ".github/workflows/ci.yml", "setup.py"):
        with pytest.raises(AuthorityError):
            assert_write_allowed(BuildRole.WRITER, ws / denied, workspace=ws)


def test_no_other_role_may_write_the_scaffold(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    for role in (BuildRole.CLONER, BuildRole.TESTER, BuildRole.COLLECTOR):
        with pytest.raises(AuthorityError):
            assert_write_allowed(role, ws / "Dockerfile", workspace=ws)
