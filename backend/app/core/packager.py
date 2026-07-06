"""Package a CerebrumDev.ai session into a deployable Cerebrum-Blocks instance."""

import os
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.session import SessionState
from .chroma_store import load_chunks, collection_exists
from .engine_discovery import _find_engine_root
from .llm_config import get_llm_config

# Absolute path to the Tinker adapter so we can copy it into deployed packages.
_TINKER_ADAPTER_SOURCE = Path(__file__).parent / "llm" / "tinker_adapter.py"

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")


def _package_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "deploy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).lower().strip("-")[:40]


def _export_vectors(session_id: str, state: SessionState) -> Dict[str, Any]:
    """Export the session's vector index to a JSON-serializable structure."""
    chunks: List[str] = []
    embeddings: List[List[float]] = []
    metadatas: List[Dict[str, Any]] = []

    if collection_exists(session_id):
        try:
            chunks, embeddings, metadatas = load_chunks(session_id)
            logger.info("Exported %d vectors from ChromaDB for %s", len(chunks), session_id)
        except Exception as exc:
            logger.warning("Could not load Chroma vectors for %s: %s", session_id, exc)

    if not chunks and state.chunks:
        chunks = state.chunks
        embeddings = state.embeddings or []
        metadatas = [{"session_id": session_id, "index": i} for i in range(len(chunks))]

    vectors = {
        "session_id": session_id,
        "domain": state.config.domain,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
        "embeddings": embeddings,
        "metadatas": metadatas,
    }
    if state.embedding_meta:
        vectors["embedding"] = {
            "provider": "zvec",
            "model": state.embedding_meta.get("model"),
            "dim": state.embedding_meta.get("dimensions"),
        }
    return vectors


def _copy_tinker_adapter(package_root: Path) -> None:
    """Copy the grounded Tinker adapter into the deployable package."""
    if not _TINKER_ADAPTER_SOURCE.exists():
        logger.warning("Tinker adapter source not found at %s", _TINKER_ADAPTER_SOURCE)
        return
    dest_dir = package_root / "app" / "core" / "llm"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_TINKER_ADAPTER_SOURCE, dest_dir / "tinker_adapter.py")
    logger.info("Copied Tinker adapter into package")


def _drop_cli_artifacts(package_root: Path, service_name: str, env_vars: Dict[str, str]) -> None:
    """Drop a cerebrum CLI folder into the package so the instance ships with a working client."""
    cli_dir = package_root / "cli"
    cli_dir.mkdir(parents=True, exist_ok=True)

    # Install script: pip-installs the bundled CLI package.
    install_sh = cli_dir / "install.sh"
    install_sh.write_text(
        "#!/bin/sh\n"
        "# Install the cerebrum CLI bundled with this instance.\n"
        "set -e\n"
        "SCRIPT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
        "pip install \"$SCRIPT_DIR/cerebrum_cli\"\n"
        "echo \"cerebrum CLI installed. Run: cerebrum --help\"\n",
        encoding="utf-8",
    )
    install_sh.chmod(0o755)

    # Pre-filled config pointing the CLI at this instance.
    config_toml = cli_dir / "config.toml"
    base_url = f"https://{service_name}.onrender.com"
    config_toml.write_text(
        f"# Cerebrum CLI configuration for this deployed instance.\n"
        f'base_url = "{base_url}"\n'
        f'api_key = "{env_vars.get("CEREBRUM_MASTER_KEY", "")}"\n'
        f'domain = "{env_vars.get("CEREBRUM_DOMAIN_KITS", "construction")}"\n'
        f'instance_name = "{service_name}"\n'
        f'session_id = ""\n',
        encoding="utf-8",
    )

    # Copy the CLI package source from the engine checkout so install.sh works offline.
    engine_root = _find_engine_root()
    engine_cli = engine_root / "cli" / "cerebrum_cli"
    if engine_cli.exists():
        shutil.copytree(engine_cli, cli_dir / "cerebrum_cli", dirs_exist_ok=True)
        logger.info("Copied cerebrum_cli source from %s into package", engine_cli)
    else:
        logger.warning(
            "Engine cerebrum_cli source not found at %s; install.sh will need network", engine_cli
        )


def _copy_or_generate_container(session_id: str, state: SessionState, package_root: Path) -> Path:
    domain = state.config.domain
    dest_dir = package_root / "app" / "containers"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{domain}.py"

    if state.container_modified_path and Path(state.container_modified_path).exists():
        shutil.copy2(state.container_modified_path, dest)
        logger.info("Copied container %s -> %s", state.container_modified_path, dest)
        return dest

    from .rule_injector import inject_rules
    try:
        generated = inject_rules(session_id, domain, state.extracted_rules or [])
        shutil.copy2(generated, dest)
        logger.info("Generated fallback container for %s", domain)
        return dest
    except Exception as exc:
        logger.warning("Could not generate container for %s: %s", domain, exc)

    dest.write_text(
        f'''"""Auto-generated domain container for {domain}."""


class {domain.title().replace("_", "")}Container:
    name = "{domain}_with_rules"
    domain = "{domain}"

    def __init__(self, config=None):
        self.config = config or {{}}
        self.rules = []

    def apply_rules(self, context):
        return context

    def run_chain(self, chain, inputs):
        result = inputs
        for connection in chain.get("connections", []):
            block = chain["blocks"][connection["from"]]
            next_block = chain["blocks"][connection["to"]]
            result = {{"from": block["id"], "to": next_block["id"], "data": result}}
        return result
''',
        encoding="utf-8",
    )
    return dest


def _write_deployed_router(package_root: Path, domain: str) -> None:
    router_dir = package_root / "app" / "routers"
    router_dir.mkdir(parents=True, exist_ok=True)
    router = router_dir / "deployed.py"
    router.write_text(
        f'''"""Deployed-session router – exposes the approved chain and container."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import numpy as np
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()
logger = logging.getLogger(__name__)

DOMAIN = os.getenv("CEREBRUM_DOMAIN", "{domain}")
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "http://127.0.0.1:8000")
CEREBRUM_MASTER_KEY = os.getenv("CEREBRUM_MASTER_KEY", "")
RAG_K = int(os.getenv("RAG_K", "3"))
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "5.0"))

# Optional grounded Tinker LoRA inference. Imported lazily so the package still
# boots when the tinker SDK is not installed.
try:
    from app.core.llm.tinker_adapter import TinkerAdapter
except Exception as _tinker_exc:
    TinkerAdapter = None  # type: ignore
    logger.debug("Tinker adapter not available in deployed runtime: %s", _tinker_exc)

BASE_DIR = Path(__file__).parent.parent.parent
CHAIN_PATH = BASE_DIR / "default_chain.json"
VECTORS_PATH = BASE_DIR / "vectors.json"

CHAIN = json.loads(CHAIN_PATH.read_text(encoding="utf-8")) if CHAIN_PATH.exists() else {{"blocks": [], "connections": []}}
try:
    _raw_vectors = VECTORS_PATH.read_text(encoding="utf-8") if VECTORS_PATH.exists() else "{{}}"
    VECTORS = json.loads(_raw_vectors)
except (json.JSONDecodeError, OSError) as _vectors_load_exc:
    logger.warning("Could not load vectors.json; treating as legacy/missing: %s", _vectors_load_exc)
    VECTORS = {{"chunks": [], "embeddings": []}}

_RAG_STATUS: Optional[Dict[str, Any]] = None


def _load_container():
    try:
        module = __import__(f"app.containers.{{DOMAIN}}", fromlist=["Container"])
        return getattr(module, f"{{DOMAIN.title().replace('_', '')}}Container", None)
    except Exception as exc:
        logger.warning("Could not load container for %s: %s", DOMAIN, exc)
        return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return np.dot(a_norm, b_norm.T)


def _retrieve(query_embedding: List[float], k: int = RAG_K) -> List[str]:
    chunks = VECTORS.get("chunks", [])
    embeddings = VECTORS.get("embeddings", [])
    if not chunks or not embeddings:
        return []
    try:
        q = np.array(query_embedding, dtype=np.float32).reshape(1, -1)
        db = np.array(embeddings, dtype=np.float32)
        scores = _cosine_similarity(q, db)[0]
        top_idx = np.argsort(scores)[::-1][:k]
        return [chunks[i] for i in top_idx if scores[i] > 0]
    except Exception as exc:
        logger.warning("RAG retrieval failed: %s", exc)
        return []


def _validate_embedding_meta() -> Dict[str, Any]:
    """Validate vectors.json embedding metadata. Does not raise."""
    try:
        if not VECTORS_PATH.exists():
            return {{"available": False, "detail": "No vectors.json found; RAG unavailable."}}

        if not isinstance(VECTORS, dict):
            return {{"available": False, "detail": "Malformed vectors.json: top-level value is not an object."}}

        embedding = VECTORS.get("embedding")
        if embedding is None:
            return {{
                "available": False,
                "detail": "Index predates embedding metadata and must be re-packaged.",
            }}

        if not isinstance(embedding, dict):
            return {{"available": False, "detail": "Malformed embedding metadata: not an object."}}

        provider = embedding.get("provider")
        model = embedding.get("model")
        dim = embedding.get("dim")

        if provider != "zvec":
            return {{"available": False, "detail": f"Unsupported embedding provider '{{provider}}'; expected 'zvec'."}}

        if not model:
            return {{"available": False, "detail": "Missing embedding model identifier."}}

        try:
            expected_dim = int(dim)
        except (TypeError, ValueError):
            return {{"available": False, "detail": "Malformed embedding dimension."}}

        embeddings = VECTORS.get("embeddings", [])
        if not embeddings:
            return {{"available": False, "detail": "No embeddings present in vectors.json."}}

        if len(embeddings[0]) != expected_dim:
            return {{
                "available": False,
                "detail": f"Embedding dimension mismatch: expected {{expected_dim}}, got {{len(embeddings[0])}}.",
            }}

        return {{
            "available": True,
            "detail": f"zvec model '{{model}}' dim {{expected_dim}} validated.",
            "provider": provider,
            "model": model,
            "dim": expected_dim,
        }}
    except Exception as exc:
        logger.warning("RAG metadata validation failed: %s", exc)
        return {{"available": False, "detail": f"RAG metadata validation failed: {{exc}}"}}


async def _ensure_rag_status() -> Dict[str, Any]:
    global _RAG_STATUS
    if _RAG_STATUS is None:
        _RAG_STATUS = _validate_embedding_meta()
    return _RAG_STATUS


@router.get("/health")
async def health():
    return {{
        "domain": DOMAIN,
        "rag": await _ensure_rag_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }}


async def _embed_query(text: str) -> Optional[List[float]]:
    """Compute a query embedding using the local zvec block."""
    rag = await _ensure_rag_status()
    if not rag.get("available", False):
        return None

    url = f"{{CEREBRUM_API_URL.rstrip('/')}}/v1/execute"
    payload = {{
        "block": "zvec",
        "input": {{"text": text, "query": text}},
        "params": {{"operation": "embed"}},
    }}
    headers: Dict[str, str] = {{"Content-Type": "application/json"}}
    if CEREBRUM_MASTER_KEY:
        headers["Authorization"] = f"Bearer {{CEREBRUM_MASTER_KEY}}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("zvec embed query failed: %s", exc)
        return None

    if data.get("status") != "success":
        logger.warning("zvec embed query returned error: %s", data.get("result"))
        return None

    result = data.get("result", {{}})
    vector = result.get("vector", [])
    if not vector:
        logger.warning("zvec embed query returned empty vector")
        return None

    expected_dim = rag.get("dim")
    if expected_dim and len(vector) != expected_dim:
        logger.warning(
            "zvec embed dimension mismatch: expected %s, got %s", expected_dim, len(vector)
        )
        return None

    return vector


async def _llm_response_text(
    message: str, history: List[Dict[str, str]], context_chunks: List[str]
) -> str:
    system = "You are a helpful assistant."
    if context_chunks:
        system += "\\n\\nRelevant context:\\n" + "\\n\\n".join(context_chunks)

    if TinkerAdapter is not None and TinkerAdapter.is_available():
        try:
            result = await TinkerAdapter.call(
                message=message,
                system_prompt=system,
                max_tokens=1024,
                temperature=0.7,
            )
            if result.get("status") == "success":
                return str(result.get("response", ""))
            logger.warning("Tinker adapter returned error: %s", result.get("error"))
        except Exception as exc:
            logger.warning("Tinker adapter call failed: %s", exc)

    messages = [{{"role": "system", "content": system}}] + history + [{{"role": "user", "content": message}}]

    if OLLAMA_URL:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{{OLLAMA_URL.rstrip('/')}}/api/chat",
                    json={{
                        "model": OLLAMA_MODEL,
                        "messages": messages,
                        "stream": False,
                        "options": {{"temperature": 0.7}},
                    }},
                )
                resp.raise_for_status()
                data = resp.json()
                return str((data.get("message") or {{}}).get("content", ""))
        except Exception as exc:
            logger.warning("Ollama chat failed: %s", exc)

    return "Chat is offline. Configure OLLAMA_URL or a Tinker fine-tuned model to restore AI responses."


def _sse_event(payload: Dict[str, Any]) -> str:
    """Canonical SSE line per Cerebrum-Blocks/docs/SSE_ENVELOPE.md."""
    return f"data: {{json.dumps(payload)}}\\n\\n"


async def _produce_tokens(
    queue: asyncio.Queue,
    message: str,
    history: List[Dict[str, str]],
    context_chunks: List[str],
) -> None:
    try:
        text = await _llm_response_text(message, history, context_chunks)
        words = text.split(" ") if text.strip() else []
        for idx, word in enumerate(words):
            trailing = " " if idx < len(words) - 1 else ""
            await queue.put(word + trailing)
    except Exception as exc:
        await queue.put(("__error__", str(exc)))
    finally:
        await queue.put(None)


async def _canonical_stream(
    message: str, history: List[Dict[str, str]]
) -> AsyncGenerator[str, None]:
    """Emit the canonical SSE envelope for a chat turn."""
    rag = await _ensure_rag_status()
    rag_available = rag.get("available", False)
    note = "" if rag_available else rag.get("detail", "RAG unavailable.")
    yield _sse_event({{"type": "start", "rag_available": rag_available, "note": note}})

    context_chunks: List[str] = []
    if rag_available:
        query_embedding = await _embed_query(message)
        if query_embedding:
            context_chunks = _retrieve(query_embedding)
            if context_chunks:
                yield _sse_event({{"type": "sources", "sources": context_chunks}})

    queue: asyncio.Queue = asyncio.Queue()
    producer = asyncio.create_task(_produce_tokens(queue, message, history, context_chunks))
    terminal = False
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield _sse_event({{"type": "heartbeat"}})
                continue

            if item is None:
                if not terminal:
                    yield _sse_event({{"type": "end", "complete": True}})
                    terminal = True
                break

            if isinstance(item, tuple) and item[0] == "__error__":
                if not terminal:
                    yield _sse_event({{"type": "error", "message": item[1]}})
                    terminal = True
                break

            yield _sse_event({{"type": "token", "content": item}})
    finally:
        if not producer.done():
            producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass
        if not terminal:
            yield _sse_event({{"type": "end", "complete": True}})


@router.get("/chain")
async def get_chain():
    return {{"domain": DOMAIN, "chain": CHAIN}}


@router.post("/run")
async def run_chain(payload: Dict[str, Any]):
    container_cls = _load_container()
    inputs = payload.get("input", {{}})
    if container_cls:
        return {{
            "status": "success",
            "domain": DOMAIN,
            "result": container_cls().run_chain(CHAIN, inputs),
        }}
    return {{"status": "error", "error": "Container not available"}}


@router.post("/chat")
async def chat(payload: Dict[str, Any]):
    message = payload.get("message", "")
    history = payload.get("history", [])
    return StreamingResponse(
        _canonical_stream(message, history),
        media_type="text/event-stream",
    )
''',
        encoding="utf-8",
    )


def _write_patch_script(package_root: Path) -> None:
    patch = package_root / "patch_blocks.py"
    patch.write_text(
        """'''Inject the deployed-session router into Cerebrum-Blocks app/main.py.'''
from pathlib import Path

main = Path('app/main.py')
text = main.read_text(encoding='utf-8')

if 'deployed' in text:
    print('deployed router already present')
    raise SystemExit(0)

# Add import inside the existing from app.routers import block
marker = 'from app.routers import (\\n'
if marker in text:
    text = text.replace(marker, marker + '    deployed,\\n')
else:
    # Fallback: insert after the import block
    text = text.replace(
        'from app.routers.metrics import _record_metrics',
        'from app.routers.metrics import _record_metrics\\nfrom app.routers import deployed',
    )
    text = text.replace(
        'app.include_router(workflow.router)\\n',
        'app.include_router(workflow.router)\\napp.include_router(deployed.router, prefix=\\"/v1/deployed\\", tags=[\\"deployed\\"])\\n',
    )
    main.write_text(text, encoding='utf-8')
    raise SystemExit(0)

# Add router include after workflow router
text = text.replace(
    'app.include_router(workflow.router)\\n',
    'app.include_router(workflow.router)\\napp.include_router(deployed.router, prefix=\\"/v1/deployed\\", tags=[\\"deployed\\"])\\n',
)

main.write_text(text, encoding='utf-8')
print('patched app/main.py with deployed router')

# Also augment the engine's root /health so Render and operators see RAG status.
health = Path('app/routers/health.py')
if health.exists():
    health_text = health.read_text(encoding='utf-8')
    if 'rag' not in health_text:
        health_text = health_text.replace(
            'from app.blocks import BLOCK_REGISTRY\\n',
            'from app.blocks import BLOCK_REGISTRY\\nfrom app.routers import deployed\\n',
        )
        if 'from app.routers import deployed' not in health_text:
            raise RuntimeError(
                "Could not patch app/routers/health.py: expected anchor "
                "'from app.blocks import BLOCK_REGISTRY' was not found."
            )
        health_text = health_text.replace(
            '''@router.get(\"/health\")\ndef health():\n    \"\"\"Health check.\"\"\"\n    return {\n        \"status\": \"healthy\",\n        \"blocks_loaded\": len(block_instances),\n        \"blocks_available\": len(BLOCK_REGISTRY),\n        \"timestamp\": datetime.now(timezone.utc).isoformat(),\n    }''',
            '''@router.get(\"/health\")\nasync def health():\n    'Health check, including deployed-session RAG status.'\n    return {\n        \"status\": \"healthy\",\n        \"blocks_loaded\": len(block_instances),\n        \"blocks_available\": len(BLOCK_REGISTRY),\n        \"rag\": await deployed._ensure_rag_status(),\n        \"timestamp\": datetime.now(timezone.utc).isoformat(),\n    }''',
        )
        if 'rag' not in health_text:
            raise RuntimeError(
                "Could not patch app/routers/health.py: expected anchor "
                "'/health route definition' was not found."
            )
        health.write_text(health_text, encoding='utf-8')
        print('patched app/routers/health.py with RAG status')
""",
        encoding="utf-8",
    )


def _write_bootstrap_script(package_root: Path) -> None:
    bootstrap = package_root / "bootstrap.py"
    bootstrap.write_text(
        '''"""Restore the session vector index into ChromaDB on first boot."""
import json
import os
from pathlib import Path

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "/app/chroma")

vectors_path = Path(__file__).parent / "vectors.json"
if not vectors_path.exists():
    print("No vectors.json found; skipping bootstrap.")
    raise SystemExit(0)

with open(vectors_path, encoding="utf-8") as f:
    data = json.load(f)

chunks = data.get("chunks", [])
embeddings = data.get("embeddings", [])
metadatas = data.get("metadatas", []) or [{"index": i} for i in range(len(chunks))]

if not chunks:
    print("No chunks to index.")
    raise SystemExit(0)

os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
try:
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    collection = client.get_or_create_collection(
        name="session_default",
        metadata={"domain": data.get("domain", "general"), "source": "deploy_bootstrap"},
    )
    ids = [f"chunk_{{i}}" for i in range(len(chunks))]
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings if embeddings and len(embeddings) == len(chunks) else None,
        metadatas=metadatas,
    )
    print(f"Bootstrapped {{len(chunks)}} chunks into ChromaDB at {{CHROMA_PERSIST_DIR}}")
except Exception as exc:
    print(f"Bootstrap failed (non-fatal): {{exc}}")
''',
        encoding="utf-8",
    )

    bootstrap_sh = package_root / "bootstrap.sh"
    bootstrap_sh.write_text(
        '#!/bin/sh\nset -e\npython bootstrap.py\n',
        encoding="utf-8",
    )


def _write_dockerfile(package_root: Path) -> None:
    dockerfile = package_root / "Dockerfile"
    dockerfile.write_text(
        '''# Deployed Cerebrum-Blocks instance with injected domain container and chain.
FROM python:3.11-slim
WORKDIR /app

# System deps for Cerebrum-Blocks (OCR, PDF, build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git build-essential gcc g++ gfortran pkg-config \\
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \\
    poppler-utils tesseract-ocr libtesseract-dev curl \\
    && rm -rf /var/lib/apt/lists/*

# Clone Cerebrum-Blocks runtime
RUN git clone --depth 1 https://github.com/bopoadz-del/Cerebrum-Blocks.git /app

# Install Python deps
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \\
    pip install --no-cache-dir -r requirements.txt

# If you are serving a Tinker fine-tuned model, the runtime needs the
# ``tinker`` package installed (it is not on PyPI). Mount or COPY the vendored
# wheel/source here and install it, e.g.:
# COPY tinker-*.whl /tmp/
# RUN pip install --no-cache-dir /tmp/tinker-*.whl

# Inject session-specific files and patch the runtime
COPY . /app
RUN python patch_blocks.py

# Restore vector index
RUN python bootstrap.py

ENV PORT=8000
EXPOSE 8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
''',
        encoding="utf-8",
    )


def _write_render_yaml(
    package_root: Path,
    service_name: str,
    env_vars: Dict[str, str],
) -> None:
    render = package_root / "render.yaml"
    env_lines = "\n".join(
        f"      - key: {key}\n        value: {value}"
        for key, value in env_vars.items()
    )
    render.write_text(
        f'''services:
  - type: web
    name: {service_name}
    env: docker
    dockerfilePath: ./Dockerfile
    healthCheckPath: /health
    plan: starter
    region: oregon
    envVars:
{env_lines}
    disk:
      name: {service_name}-data
      mountPath: /app/data
      sizeGB: 1
''',
        encoding="utf-8",
    )


def _write_dotenv(package_root: Path, env_vars: Dict[str, str]) -> None:
    dotenv = package_root / ".env"
    dotenv.write_text(
        "\n".join(f'{key}={value}' for key, value in env_vars.items()),
        encoding="utf-8",
    )


def package_session(state: SessionState, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Create a deployable package for *state* and return metadata."""
    session_id = state.session_id
    domain = state.config.domain
    package_root = _package_dir(session_id) / "package"
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True, exist_ok=True)

    # 1. Container
    _copy_or_generate_container(session_id, state, package_root)

    # 2. Approved chain
    chain_path = package_root / "default_chain.json"
    chain_path.write_text(
        json.dumps(state.proposed_chain or {"blocks": [], "connections": []}, indent=2),
        encoding="utf-8",
    )

    # 3. Exported vectors
    vectors = _export_vectors(session_id, state)
    (package_root / "vectors.json").write_text(
        json.dumps(vectors, indent=2),
        encoding="utf-8",
    )

    # 4. Original documents for re-indexing / audit
    docs_dir = package_root / "data" / "docs"
    session_files = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    if session_files.exists():
        docs_dir.mkdir(parents=True, exist_ok=True)
        for f in session_files.iterdir():
            if f.is_file():
                shutil.copy2(f, docs_dir / f.name)

    # 5. Runtime integration
    _copy_tinker_adapter(package_root)
    _write_deployed_router(package_root, domain)
    _write_patch_script(package_root)
    _write_bootstrap_script(package_root)
    _write_dockerfile(package_root)

    # 6. Env + Render blueprint
    service_name = f"cerebrumdev-{_safe_name(domain)}-{_safe_name(session_id)[:16]}"
    deploy_api_key = api_key or f"cd-deploy-{os.urandom(16).hex()}"
    llm_cfg = get_llm_config()
    env_vars = {
        "CEREBRUM_DOMAIN_KITS": domain,
        "CEREBRUM_MASTER_KEY": deploy_api_key,
        "CEREBRUM_API_KEY_CDEV": deploy_api_key,
        "CORS_ORIGINS": "*",
        "CHROMA_PERSIST_DIR": "/app/chroma",
        "LLM_PROVIDER": llm_cfg["provider"],
        "OLLAMA_URL": os.getenv("OLLAMA_URL", ""),
        "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
        "QWEN_BASE_URL": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "QWEN_MODEL": os.getenv("QWEN_MODEL", "qwen-plus"),
        "ENV": "production",
        "PORT": "8000",
    }
    if llm_cfg["provider"] == "qwen":
        env_vars["QWEN_API_KEY"] = llm_cfg["api_key"]
    elif llm_cfg["provider"] == "moonshot":
        env_vars["CEREBRUM_LLM_API_KEY"] = llm_cfg["api_key"]
        env_vars["CEREBRUM_LLM_BASE_URL"] = llm_cfg["base_url"]
        env_vars["CEREBRUM_LLM_MODEL"] = llm_cfg["model"]
    if state.training_job.status == "completed" and state.training_job.fine_tuned_model_id:
        # A completed Tinker LoRA produces a tinker:// path; prefer the grounded
        # adapter when the deployed runtime is configured for Tinker inference.
        env_vars["OLLAMA_MODEL"] = state.training_job.fine_tuned_model_id
        if state.training_job.fine_tuned_model_id.startswith("tinker://"):
            env_vars["GROUNDED_ADAPTER_ENABLED"] = "true"
            env_vars["GROUNDED_ADAPTER_TINKER_PATH"] = state.training_job.fine_tuned_model_id
            env_vars.setdefault("GROUNDED_ADAPTER_REWRITE_PASS", "false")
            env_vars.setdefault("GROUNDED_ADAPTER_TIMEOUT", "60")
            # The deployed runtime needs to authenticate with Tinker Cloud.
            tinker_key = os.getenv("TINKER_API_KEY", "")
            if tinker_key:
                env_vars["TINKER_API_KEY"] = tinker_key
    _write_dotenv(package_root, env_vars)
    _write_render_yaml(package_root, service_name, env_vars)
    _drop_cli_artifacts(package_root, service_name, env_vars)

    # 7. Zip package
    zip_path = _package_dir(session_id) / "package.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in package_root.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(package_root))

    logger.info("Packaged session %s into %s (%d bytes)", session_id, zip_path, zip_path.stat().st_size)
    return {
        "package_dir": str(package_root),
        "zip_path": str(zip_path),
        "service_name": service_name,
        "api_key": deploy_api_key,
        "env_vars": env_vars,
    }
