"""Domain smoke contract test for the real estate concept flow.

This test proves the full flow without real LLM credentials:

  user need -> proposed chain -> validation -> approval -> platform package -> package inspection

It patches the chain generator to return a deterministic real estate chain and then
exercises the real chat, preview, approve, deploy, and download endpoints.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.session_store import create_session, get_session


client = TestClient(app)


# Deterministic LLM response for the real estate document analysis concept.
REAL_ESTATE_CHAIN: Dict[str, Any] = {
    "message": "Here is a real estate document analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "real_estate_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag tenancy compliance issues",
        "Always flag zoning or land use restrictions",
        "Always flag strata or HOA levy risks",
        "Always flag missing disclosure statements",
    ],
}


@pytest.fixture
def fake_real_estate_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a real estate kit."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    kit_root = engine_root / "block_store" / "kits" / "real_estate"
    bundle = kit_root / "bundle"

    # Kit artifacts
    (bundle / "app" / "blocks").mkdir(parents=True)
    (bundle / "app" / "blocks" / "real_estate_v2.py").write_text(
        "class RealEstateBlockV2:\n    name = 'real_estate_v2'\n",
        encoding="utf-8",
    )
    (bundle / "app" / "containers").mkdir(parents=True)
    (bundle / "app" / "containers" / "real_estate.py").write_text(
        "class RealEstateContainer:\n    name = 'real_estate'\n",
        encoding="utf-8",
    )
    manifest = {
        "id": "real_estate",
        "container": {"class": "app.containers.real_estate.RealEstateContainer"},
        "blocks": ["pdf", "ocr", "chat", "image", "real_estate_v2"],
        "artifacts": [
            {"src": "app/containers/real_estate.py", "dest": "app/containers/real_estate.py"},
            {"src": "app/blocks/real_estate_v2.py", "dest": "app/blocks/real_estate_v2.py"},
        ],
    }
    (kit_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Engine runtime pieces required by the packager
    cli_pkg = engine_root / "cli" / "cerebrum_cli"
    cli_pkg.mkdir(parents=True)
    (cli_pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    (engine_root / "app" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / "app" / "main.py").write_text("app\n", encoding="utf-8")
    (engine_root / "block_store" / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / "block_store" / "README.md").write_text("store\n", encoding="utf-8")

    # Point the packager at the fake engine
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    return engine_root


async def _mock_generate_chain_suggestion(*args, **kwargs) -> Dict[str, Any]:
    """Return the deterministic real estate chain."""
    return REAL_ESTATE_CHAIN


def test_real_estate_domain_smoke_contract(fake_real_estate_engine: Path, monkeypatch, tmp_path: Path):
    """Full real estate concept flow: chat -> preview -> approve -> package -> inspect."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))

    # 1. Create a real estate session
    session = create_session(session_id="sess_real_estate_smoke", user_id="test_user")
    session.config.domain = "real_estate"

    # 2. Submit the real estate workflow prompt with patched chain generator
    #    and block registry (so no real Cerebrum-Blocks backend is needed).
    fake_registry = {b["id"]: {"name": b["id"]} for b in REAL_ESTATE_CHAIN["chain"]["blocks"]}
    with patch(
        "app.routers.chat.generate_chain_suggestion",
        new=_mock_generate_chain_suggestion,
    ), patch(
        "app.routers.chat.fetch_block_registry",
        return_value=fake_registry,
    ):
        response = client.post(
            f"/v1/sessions/{session.session_id}/chat",
            json={"message": "I want to build a real estate document analysis platform."},
        )
    assert response.status_code == 200

    # 3. Assert proposed chain was saved and contains required blocks
    state = get_session(session.session_id)
    assert state is not None
    assert state.proposed_chain is not None
    assert state.validation_passed is True

    block_ids = {b["id"] for b in state.proposed_chain["blocks"]}
    assert "real_estate_v2" in block_ids
    assert "chat" in block_ids
    assert block_ids & {"document_engine", "pdf", "vector_search"}

    # 4. Chain preview endpoint returns the chain
    response = client.get(f"/v1/sessions/{session.session_id}/chain/preview")
    assert response.status_code == 200
    preview = response.json()
    assert preview["chain"] == state.proposed_chain

    # 5. Approve the chain
    response = client.post(
        f"/v1/sessions/{session.session_id}/chain/approve",
        json={"approve": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["chain_approved"] is True

    # 6. Generate platform package
    response = client.post(
        f"/v1/sessions/{session.session_id}/deploy?target=platform"
    )
    assert response.status_code == 200
    deploy_data = response.json()
    assert deploy_data["status"] == "packaged"
    assert "variant=platform" in deploy_data["download_url"]

    # 7. Download and inspect the zip
    response = client.get(
        f"/v1/sessions/{session.session_id}/deploy/package?variant=platform"
    )
    assert response.status_code == 200
    zip_bytes = response.content
    assert len(zip_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())

        # Directory checks: ZipFile lists files, so verify file paths under engine/
        assert any(n.startswith("engine/app/") for n in names)
        assert any(n.startswith("engine/block_store/") for n in names)
        assert "app/containers/real_estate.py" in names
        assert "app/blocks/real_estate_v2.py" in names
        assert "default_chain.json" in names
        assert "build_metadata.json" in names
        assert "Dockerfile" in names

        # Dockerfile assertions
        dockerfile = zf.read("Dockerfile").decode("utf-8")
        assert "COPY engine/ /app" in dockerfile
        assert "git clone" not in dockerfile.lower()
        assert "2>/dev/null" not in dockerfile
        assert "|| true" not in dockerfile

        # build_metadata assertions
        build_metadata = json.loads(zf.read("build_metadata.json").decode("utf-8"))
        engine_meta = build_metadata["engine"]
        assert engine_meta["vendored"] is True
        assert engine_meta["vendored_path"] == "engine/"
        assert engine_meta["ref"] and engine_meta["ref"] != ""
        assert "commit_sha" in engine_meta
