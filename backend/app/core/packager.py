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

    return {
        "session_id": session_id,
        "domain": state.config.domain,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
        "embeddings": embeddings,
        "metadatas": metadatas,
    }


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

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import numpy as np
from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)

DOMAIN = os.getenv("CEREBRUM_DOMAIN", "{domain}")
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "")
CEREBRUM_API_KEY = os.getenv("CEREBRUM_API_KEY", "")
RAG_K = int(os.getenv("RAG_K", "3"))

BASE_DIR = Path(__file__).parent.parent.parent
CHAIN_PATH = BASE_DIR / "default_chain.json"
VECTORS_PATH = BASE_DIR / "vectors.json"

CHAIN = json.loads(CHAIN_PATH.read_text(encoding="utf-8")) if CHAIN_PATH.exists() else {{"blocks": [], "connections": []}}
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8")) if VECTORS_PATH.exists() else {{"chunks": [], "embeddings": []}}


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

    context_chunks = []
    if OLLAMA_URL and VECTORS.get("embeddings"):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                emb_resp = await client.post(
                    f"{{OLLAMA_URL.rstrip('/')}}/api/embeddings",
                    json={{"model": OLLAMA_MODEL, "prompt": message}},
                )
                emb_resp.raise_for_status()
                emb = emb_resp.json().get("embedding", [])
                if emb:
                    context_chunks = _retrieve(emb)
        except Exception as exc:
            logger.warning("Embedding for RAG failed: %s", exc)

    system = "You are a helpful assistant."
    if context_chunks:
        system += "\\n\\nRelevant context:\\n" + "\\n\\n".join(context_chunks)

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
                text = (data.get("message") or {{}}).get("content", "")
                return {{"text": text, "provider": "ollama", "model": OLLAMA_MODEL}}
        except Exception as exc:
            logger.warning("Ollama chat failed: %s", exc)

    return {{
        "text": "Chat is offline. Configure OLLAMA_URL to restore AI responses.",
        "provider": "offline_template",
        "model": "offline",
    }}
''',
        encoding="utf-8",
    )


def _write_patch_script(package_root: Path) -> None:
    patch = package_root / "patch_blocks.py"
    patch.write_text(
        '''"""Inject the deployed-session router into Cerebrum-Blocks app/main.py."""
from pathlib import Path

main = Path("app/main.py")
text = main.read_text(encoding="utf-8")

if "deployed" in text:
    print("deployed router already present")
    raise SystemExit(0)

# Add import inside the existing from app.routers import block
marker = "from app.routers import (\\n"
if marker in text:
    text = text.replace(marker, marker + "    deployed,\\n")
else:
    # Fallback: insert after the import block
    text = text.replace(
        "from app.routers.metrics import _record_metrics",
        "from app.routers.metrics import _record_metrics\\nfrom app.routers import deployed",
    )
    text = text.replace(
        "app.include_router(workflow.router)\\n",
        "app.include_router(workflow.router)\\napp.include_router(deployed.router, prefix=\\"/v1/deployed\\", tags=[\\"deployed\\"])\\n",
    )
    main.write_text(text, encoding="utf-8")
    raise SystemExit(0)

# Add router include after workflow router
text = text.replace(
    "app.include_router(workflow.router)\\n",
    "app.include_router(workflow.router)\\napp.include_router(deployed.router, prefix=\\"/v1/deployed\\", tags=[\\"deployed\\"])\\n",
)

main.write_text(text, encoding="utf-8")
print("patched app/main.py with deployed router")
''',
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
        json.dumps(state.proposed_chain or {{"blocks": [], "connections": []}}, indent=2),
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
    _write_deployed_router(package_root, domain)
    _write_patch_script(package_root)
    _write_bootstrap_script(package_root)
    _write_dockerfile(package_root)

    # 6. Env + Render blueprint
    service_name = f"cerebrumdev-{_safe_name(domain)}-{_safe_name(session_id)[:16]}"
    deploy_api_key = api_key or f"cd-deploy-{os.urandom(16).hex()}"
    env_vars = {
        "CEREBRUM_DOMAIN_KITS": domain,
        "CEREBRUM_MASTER_KEY": deploy_api_key,
        "CEREBRUM_API_KEY_CDEV": deploy_api_key,
        "CORS_ORIGINS": "*",
        "CHROMA_PERSIST_DIR": "/app/chroma",
        "OLLAMA_URL": os.getenv("OLLAMA_URL", ""),
        "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
        "ENV": "production",
        "PORT": "8000",
    }
    _write_dotenv(package_root, env_vars)
    _write_render_yaml(package_root, service_name, env_vars)

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
