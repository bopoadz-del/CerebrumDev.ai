import asyncio
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict

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


def test_package_empty_proposed_chain_fallback():
    """A None/empty proposed_chain must serialize as a valid empty chain."""
    state = _make_state()
    state.proposed_chain = None
    result = package_session(state)

    chain_path = Path(result["package_dir"]) / "default_chain.json"
    assert chain_path.exists()
    chain = json.loads(chain_path.read_text(encoding="utf-8"))
    assert chain == {"blocks": [], "connections": []}


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


def test_package_cli_config_stamps_deployed_mode(fake_engine_checkout: Path, monkeypatch):
    """The packaged CLI config.toml declares mode = 'deployed'."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()
    result = package_session(state)

    config_path = Path(result["package_dir"]) / "cli" / "config.toml"
    assert config_path.exists()
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    assert cfg.get("mode") == "deployed"


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


def test_vectors_json_includes_embedding_meta_for_fallback_provider(fake_engine_checkout: Path, monkeypatch):
    """Fallback-indexed sessions still stamp an embedding stanza in vectors.json."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()
    state.embedding_meta = {
        "provider": "hash_fallback",
        "backend": "hash_fallback",
        "dimensions": 256,
        "model": "hash_fallback",
    }
    state.chunks = ["chunk one", "chunk two"]
    state.embeddings = [[0.1] * 256, [0.2] * 256]
    result = package_session(state)

    package_dir = Path(result["package_dir"])
    vectors = json.loads((package_dir / "vectors.json").read_text(encoding="utf-8"))
    assert vectors["embedding"] == {
        "provider": "hash_fallback",
        "model": "hash_fallback",
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


def test_root_health_patch_fails_loudly_when_anchor_missing(tmp_path: Path):
    """patch_blocks.py raises if the engine health file no longer matches expected anchors."""
    package_root = tmp_path / "package"
    package_root.mkdir()
    from app.core.packager import _write_patch_script

    _write_patch_script(package_root)

    engine = tmp_path / "engine"
    (engine / "app/routers").mkdir(parents=True)
    (engine / "app/__init__.py").write_text("", encoding="utf-8")
    (engine / "app/routers/__init__.py").write_text("", encoding="utf-8")
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
    # Altered health.py: missing the BLOCK_REGISTRY import and the expected /health shape.
    (engine / "app/routers/health.py").write_text(
        'from datetime import datetime, timezone\n'
        'from fastapi import APIRouter\n'
        '\n'
        'router = APIRouter()\n'
        '\n'
        '@router.get("/health")\n'
        'async def health():\n'
        '    return {"status": "ok"}\n',
        encoding="utf-8",
    )
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
    assert result.returncode != 0
    assert "Could not patch app/routers/health.py" in result.stderr
    assert "from app.blocks import BLOCK_REGISTRY" in result.stderr



def _load_deployed_router(package_root: Path):
    """Load the generated deployed router from *package_root* in a fresh module."""
    router_path = package_root / "app" / "routers" / "deployed.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("deployed_router_under_test", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(coro):
    """Run an async coroutine in a sync test."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_package_cli_config_stamps_deployed_mode(fake_engine_checkout: Path, monkeypatch):
    """The packaged CLI config.toml declares mode = 'deployed'."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()
    result = package_session(state)

    config_path = Path(result["package_dir"]) / "cli" / "config.toml"
    assert config_path.exists()
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    assert cfg.get("mode") == "deployed"


def test_deployed_router_health_reports_rag_mismatch(tmp_path: Path):
    """Fallback-indexed vectors + zvec query embedder -> RAG unavailable with detail."""
    from app.core.packager import _write_deployed_router

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_deployed_router(package_root, "construction")
    (package_root / "default_chain.json").write_text(
        json.dumps({"blocks": [], "connections": []}), encoding="utf-8"
    )
    (package_root / "vectors.json").write_text(
        json.dumps(
            {
                "chunks": ["fallback chunk"],
                "embeddings": [[0.1] * 256],
                "embedding": {
                    "provider": "hash_fallback",
                    "model": "hash_fallback",
                    "dim": 256,
                },
            }
        ),
        encoding="utf-8",
    )

    mod = _load_deployed_router(package_root)

    async def _fake_probe():
        return {"provider": "zvec", "model": "test-model", "dim": 8}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, "_probe_query_embedder", _fake_probe)
    try:
        health = _run(mod.health())
        assert health["rag"]["available"] is False
        assert "hash_fallback" in health["rag"]["detail"]
        assert "zvec" in health["rag"]["detail"]

        events = _run(_collect_stream(mod._canonical_stream("query", [])))
        parsed = [json.loads(line[len("data: ") :]) for line in events]
        assert parsed[0]["type"] == "start"
        assert parsed[0]["rag_available"] is False
        assert "hash_fallback" in parsed[0]["note"]
        assert all(e["type"] != "sources" for e in parsed)
    finally:
        monkeypatch.undo()


def test_deployed_router_emits_sources_before_tokens_on_match(tmp_path: Path):
    """Same-embedder index and query -> sources event precedes token events."""
    from app.core.packager import _write_deployed_router

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_deployed_router(package_root, "construction")
    (package_root / "default_chain.json").write_text(
        json.dumps({"blocks": [], "connections": []}), encoding="utf-8"
    )
    (package_root / "vectors.json").write_text(
        json.dumps(
            {
                "chunks": ["hello world chunk"],
                "embeddings": [[0.1] * 8],
                "embedding": {
                    "provider": "zvec",
                    "model": "test-model",
                    "dim": 8,
                },
            }
        ),
        encoding="utf-8",
    )

    mod = _load_deployed_router(package_root)

    async def _fake_probe():
        return {"provider": "zvec", "model": "test-model", "dim": 8}

    async def _fake_embed(text: str):
        return [0.1] * 8

    async def _fake_llm(message: str, history, context_chunks):
        return "response text"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, "_probe_query_embedder", _fake_probe)
    monkeypatch.setattr(mod, "_embed_query", _fake_embed)
    monkeypatch.setattr(mod, "_llm_response_text", _fake_llm)
    try:
        events = _run(_collect_stream(mod._canonical_stream("hello", [])))
        parsed = [json.loads(line[len("data: ") :]) for line in events if line.startswith("data: ")]
        types = [e["type"] for e in parsed]
        assert types[0] == "start"
        assert parsed[0]["rag_available"] is True
        assert "sources" in types
        sources_idx = types.index("sources")
        token_idx = types.index("token")
        assert sources_idx < token_idx
        assert parsed[sources_idx]["sources"] == ["hello world chunk"]
    finally:
        monkeypatch.undo()


async def _collect_stream(agen):
    """Helper to drain an async generator into a list."""
    return [item async for item in agen]


def _load_dotenv(package_dir: Path) -> dict:
    """Parse a simple KEY=VALUE .env file."""
    env: Dict[str, str] = {}
    dotenv = package_dir / ".env"
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def _engine_auth_validate(key: str, env: Dict[str, str]) -> bool:
    """Mirror the production key-loading path in Cerebrum-Blocks app/core/auth.py."""
    valid = {env.get("CEREBRUM_MASTER_KEY", ""), env.get("CB_DEV_KEY", "")}
    for k, v in env.items():
        if k.startswith("CEREBRUM_API_KEY_") and v:
            valid.add(v)
    return key in valid and key != ""


def test_cloud_auth_single_source_of_truth(fake_engine_checkout: Path, monkeypatch):
    """The minted key is wired consistently across .env and CLI config."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()

    result = package_session(state)
    deploy_key = result["api_key"]
    package_dir = Path(result["package_dir"])

    env = _load_dotenv(package_dir)
    assert env["CEREBRUM_MASTER_KEY"] == deploy_key
    assert env["CB_DEV_KEY"] == deploy_key
    assert env["CEREBRUM_API_KEY_CDEV"] == deploy_key

    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(package_dir / "cli" / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    assert cfg["api_key"] == deploy_key


def test_cloud_auth_boots_with_its_own_key(fake_engine_checkout: Path, monkeypatch):
    """The generated cloud/edge package authenticates with its own key and rejects others."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_engine_checkout))
    state = _make_state()

    result = package_session(state)
    deploy_key = result["api_key"]
    env = _load_dotenv(Path(result["package_dir"]))

    assert _engine_auth_validate(deploy_key, env) is True
    assert _engine_auth_validate("cb_dev_key", env) is False
    assert _engine_auth_validate("wrong-key", env) is False
