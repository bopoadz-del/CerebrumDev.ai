import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from app.core.packager import package_session
from app.core import session_store
from app.models.session import SessionState, TrainingJob


@pytest.fixture(autouse=True)
def _use_temp_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("TINKER_API_KEY", "test-tinker-key")
    import app.core.packager as packager
    import app.core.chroma_store as chroma_store
    monkeypatch.setattr(packager, "STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(chroma_store, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    chroma_store._chroma_client = None


@pytest.fixture
def fake_engine_checkout(tmp_path: Path) -> Path:
    """Create a fake Cerebrum-Blocks engine checkout with the relocated CLI."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    cli_pkg = engine_root / "cli" / "cerebrum_cli"
    cli_pkg.mkdir(parents=True)
    (cli_pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    (cli_pkg / "marker_from_engine.txt").write_text("engine\n", encoding="utf-8")
    return engine_root


def _make_state(session_id: str = "sess_pkg") -> SessionState:
    state = SessionState(session_id=session_id, user_id="u1")
    state.phase = 4
    state.proposed_chain = {"blocks": [], "connections": []}
    return state


def test_package_copies_tinker_adapter():
    state = _make_state()
    result = package_session(state)
    adapter = Path(result["package_dir"]) / "app" / "core" / "llm" / "tinker_adapter.py"
    assert adapter.exists()


def test_package_sets_grounded_adapter_env_for_tinker_model():
    state = _make_state()
    state.training_job = TrainingJob(
        provider="tinker",
        job_id="job-1",
        status="completed",
        fine_tuned_model_id="tinker://sess_pkg:train:0/sampler_weights/adapter",
        dataset_size=12,
        started_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    result = package_session(state)
    env = result["env_vars"]
    assert env["GROUNDED_ADAPTER_ENABLED"] == "true"
    assert env["GROUNDED_ADAPTER_TINKER_PATH"] == state.training_job.fine_tuned_model_id
    assert "TINKER_API_KEY" in env


def test_package_does_not_set_tinker_env_for_untrained_session():
    state = _make_state()
    result = package_session(state)
    env = result["env_vars"]
    assert env.get("GROUNDED_ADAPTER_ENABLED") != "true"
    assert "GROUNDED_ADAPTER_TINKER_PATH" not in env


def test_package_cli_sourced_from_engine(fake_engine_checkout: Path, monkeypatch):
    """The packaged CLI is copied from the engine checkout."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()
    result = package_session(state)

    with zipfile.ZipFile(result["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "cli/cerebrum_cli/__init__.py" in names
        assert "cli/cerebrum_cli/marker_from_engine.txt" in names
        assert zf.read("cli/cerebrum_cli/marker_from_engine.txt").decode().strip() == "engine"
        assert "cli/install.sh" in names
        assert "cli/config.toml" in names


def test_find_engine_root_falls_back_to_sibling_checkout(monkeypatch, tmp_path: Path):
    """When CEREBRUM_BLOCKS_ROOT is unset, the sibling Cerebrum-Blocks checkout is discovered."""
    from app.core.engine_discovery import _find_engine_root

    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    project_root = tmp_path / "CerebrumDev.ai"
    sibling = tmp_path / "Cerebrum-Blocks"
    sibling.mkdir()
    anchor = project_root / "backend" / "app" / "core" / "engine_discovery.py"
    assert _find_engine_root(anchor) == sibling


def test_vectors_json_includes_embedding_meta(fake_engine_checkout: Path, monkeypatch):
    """Packaged vectors.json contains the embedding-space metadata stanza."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()
    state.embedding_meta = {
        "backend": "model2vec",
        "dimensions": 256,
        "model": "minishlab/potion-base-8M",
    }
    result = package_session(state)

    package_dir = Path(result["package_dir"])
    vectors = json.loads((package_dir / "vectors.json").read_text(encoding="utf-8"))
    assert vectors["embedding"] == {
        "provider": "zvec",
        "model": "minishlab/potion-base-8M",
        "dim": 256,
    }


def test_snapshot_rehydration_restores_embedding_meta(fake_engine_checkout: Path, monkeypatch, tmp_path: Path):
    """After clearing memory, reloading from snapshot restores embedding_meta and packages it."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    # Ensure session persistence uses the temp storage.
    import app.core.session_persistence as sp
    monkeypatch.setattr(sp, "STORAGE_PATH", str(tmp_path / "storage"))

    state = session_store.create_session("sess_rehydrate", "u1")
    state.embedding_meta = {
        "backend": "model2vec",
        "dimensions": 256,
        "model": "minishlab/potion-base-8M",
    }
    state.chunks = ["chunk one", "chunk two"]
    state.embeddings = [[0.1] * 256, [0.2] * 256]
    session_store.update_session("sess_rehydrate", state)

    # Simulate restart: drop in-memory store and reload.
    session_store._session_store.clear()
    reloaded = session_store.get_session("sess_rehydrate")
    assert reloaded is not None
    assert reloaded.embedding_meta == state.embedding_meta

    result = package_session(reloaded)
    vectors = json.loads((Path(result["package_dir"]) / "vectors.json").read_text(encoding="utf-8"))
    assert vectors["embedding"] == {
        "provider": "zvec",
        "model": "minishlab/potion-base-8M",
        "dim": 256,
    }


def test_chroma_rehydration_restores_embedding_meta(fake_engine_checkout: Path, monkeypatch, tmp_path: Path):
    """Rehydrating a session from its ChromaDB collection restores embedding_meta."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    import app.core.chroma_store as chroma_store
    import app.core.session_persistence as sp
    monkeypatch.setattr(chroma_store, "STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(sp, "STORAGE_PATH", str(tmp_path / "storage"))

    session_store.create_session("sess_chroma", "u1")
    embedding_meta = {
        "backend": "model2vec",
        "dimensions": 8,
        "model": "minishlab/potion-base-8M",
    }
    ok = chroma_store.store_chunks(
        "sess_chroma",
        ["chunk one", "chunk two"],
        embeddings=[[0.1] * 8, [0.2] * 8],
        embedding_meta=embedding_meta,
    )
    assert ok

    # Remove the JSON snapshot so get_session is forced to rehydrate from ChromaDB.
    state_path = sp._state_path("sess_chroma")
    if state_path.exists():
        state_path.unlink()
    backup_path = sp._backup_path("sess_chroma")
    if backup_path.exists():
        backup_path.unlink()

    session_store._session_store.clear()
    reloaded = session_store.get_session("sess_chroma")
    assert reloaded is not None
    assert reloaded.embedding_meta == embedding_meta


def test_root_health_reports_rag_status_after_patch(tmp_path: Path):
    """patch_blocks.py augments the engine's root /health with the deployed RAG status."""
    package_root = tmp_path / "package"
    package_root.mkdir()
    from app.core.packager import _write_patch_script

    _write_patch_script(package_root)

    # Build a minimal fake engine checkout that resembles Cerebrum-Blocks.
    engine = tmp_path / "engine"
    (engine / "app/routers").mkdir(parents=True)
    (engine / "app/__init__.py").write_text("", encoding="utf-8")
    (engine / "app/routers/__init__.py").write_text("", encoding="utf-8")
    (engine / "app/blocks.py").write_text(
        "BLOCK_REGISTRY = {}\nblock_instances = []\n", encoding="utf-8"
    )
    (engine / "app/dependencies.py").write_text(
        "block_instances = []\n", encoding="utf-8"
    )
    (engine / "app/main.py").write_text(
        'from fastapi import FastAPI\n'
        'from app.routers import (\n'
        '    workflow,\n'
        '    health,\n'
        ')\n'
        'app = FastAPI()\n'
        'app.include_router(workflow.router)\n'
        'app.include_router(health.router)\n',
        encoding="utf-8",
    )
    (engine / "app/routers/health.py").write_text(
        'from datetime import datetime, timezone\n'
        'from fastapi import APIRouter\n'
        'from app.blocks import BLOCK_REGISTRY\n'
        'from app.dependencies import block_instances\n'
        '\n'
        'router = APIRouter()\n'
        '\n'
        '@router.get("/health")\n'
        'def health():\n'
        '    """Health check."""\n'
        '    return {\n'
        '        "status": "healthy",\n'
        '        "blocks_loaded": len(block_instances),\n'
        '        "blocks_available": len(BLOCK_REGISTRY),\n'
        '        "timestamp": datetime.now(timezone.utc).isoformat(),\n'
        '    }\n',
        encoding="utf-8",
    )
    # A fake deployed router that reports a degraded RAG state.
    (engine / "app/routers/deployed.py").write_text(
        'from fastapi import APIRouter\n'
        '\n'
        'router = APIRouter()\n'
        '\n'
        'async def _ensure_rag_status():\n'
        '    return {"available": False, "detail": "degraded by test"}\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(package_root / "patch_blocks.py")],
        cwd=engine,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    # Verify the patched root /health in an isolated subprocess so we don't
    # pollute this process's sys.modules with the fake engine package.
    probe_script = engine / "probe_health.py"
    probe_script.write_text(
        "import asyncio, json\n"
        "from app.routers import health as patched_health\n"
        "print(json.dumps(asyncio.run(patched_health.health())))\n",
        encoding="utf-8",
    )
    probe = subprocess.run(
        [sys.executable, str(probe_script)],
        cwd=engine,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    response = json.loads(probe.stdout)
    assert response["status"] == "healthy"
    assert response["rag"]["available"] is False
    assert "degraded by test" in response["rag"]["detail"]
