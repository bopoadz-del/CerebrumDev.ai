"""Verify the generated automotive platform inherits The_Fork's Drive wiring.

PR 1 does not rebuild The_Fork's Drive implementation; it parameterises the
existing implementation through the platform manifest and confirms the
generated package still contains OAuth, folder-picker and project-import
routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.platform_generator.fork_baseline import FORK_BASELINE_COMMIT
from app.platform_generator.generator import PlatformGenerator


@pytest.fixture
def sample_manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "platform_id": "automotive-safety-intelligence",
        "platform_name": "Automotive Safety Intelligence",
        "domain": "automotive",
        "domain_kit": "automotive",
        "domain_block": "automotive_v2",
        "formula_block": "formula_executor_v2",
        "foundation_rag_pack": "automotive_core_rag_v1",
        "foundation_collection": "automotive_core_v1",
        "client_overlay_enabled": True,
        "client_collection_strategy": "project_scoped",
        "reference_identifiers": [
            "nhtsa_campaign_number",
            "make",
            "model",
            "model_year",
        ],
        "branding": {
            "product_name": "Automotive Safety Intelligence",
            "workspace_name": "Vehicle Safety Workspace",
            "foundation_name": "Automotive Core Knowledge",
        },
        "features": {
            "google_drive": {
                "enabled": True,
                "multi_user": True,
                "personal_connections": True,
                "organisation_shared_bindings": True,
                "default_scope": "read_only",
            }
        },
    }


def _make_minimal_fork(src: Path) -> None:
    """Create a tiny The_Fork-shaped tree for fast generator tests."""
    (src / "app" / "routers").mkdir(parents=True)
    (src / "alembic").mkdir()
    (src / "frontend").mkdir()
    (src / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
    (src / "render.yaml").write_text("services: []\n", encoding="utf-8")
    (src / "app" / "main.py").write_text(
        'from fastapi import FastAPI\n'
        'from app.routers import drive\n'
        'app = FastAPI()\n'
        'app.include_router(drive.router)\n',
        encoding="utf-8",
    )
    (src / "app" / "routers" / "drive.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter()\n'
        '@router.get("/v1/drive/callback")\n'
        'async def callback(): pass\n'
        '@router.get("/v1/drive/files")\n'
        'async def files(): pass\n'
        '@router.post("/v1/projects/{project_id}/drive/index-folder")\n'
        'async def index_folder(): pass\n',
        encoding="utf-8",
    )
    (src / ".env.example").write_text(
        "GOOGLE_CLIENT_ID=\nGOOGLE_CLIENT_SECRET=\nGOOGLE_REDIRECT_URI=\n",
        encoding="utf-8",
    )


def test_generated_package_inherits_drive_routes(
    sample_manifest: dict,
    tmp_path: Path,
):
    fork_src = tmp_path / "fork"
    blocks_src = tmp_path / "blocks"
    output = tmp_path / "generated"
    _make_minimal_fork(fork_src)
    blocks_src.mkdir()
    (blocks_src / ".git").mkdir()

    with (
        patch.object(PlatformGenerator, "_verify_fork_baseline", return_value=FORK_BASELINE_COMMIT),
        patch.object(PlatformGenerator, "_verify_fork_layout", lambda _self: None),
        patch.object(PlatformGenerator, "_blocks_commit", return_value="abc123"),
    ):
        gen = PlatformGenerator.from_dict(
            sample_manifest,
            fork_path=fork_src,
            blocks_path=blocks_src,
        )
        gen.generate(output)

    manifest_path = output / "platform_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["features"]["google_drive"]["enabled"] is True
    assert manifest["features"]["google_drive"]["multi_user"] is True

    drive_router = output / "app" / "routers" / "drive.py"
    assert drive_router.exists(), "The_Fork Drive router must be copied into the package"
    drive_source = drive_router.read_text(encoding="utf-8")
    assert "/v1/drive/callback" in drive_source
    assert "/v1/drive/files" in drive_source
    assert "/v1/projects/{project_id}/drive/index-folder" in drive_source

    main_file = output / "app" / "main.py"
    main_source = main_file.read_text(encoding="utf-8")
    assert "app.include_router(drive.router)" in main_source

    env_example = output / ".env.example"
    env_source = env_example.read_text(encoding="utf-8")
    assert "GOOGLE_CLIENT_ID=" in env_source
    assert "GOOGLE_CLIENT_SECRET=" in env_source
    assert "GOOGLE_REDIRECT_URI=" in env_source
