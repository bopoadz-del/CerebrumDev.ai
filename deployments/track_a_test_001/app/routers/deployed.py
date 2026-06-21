"""Deployed-session router – exposes the approved chain and container."""

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

DOMAIN = os.getenv("CEREBRUM_DOMAIN", "construction")
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "")
CEREBRUM_API_KEY = os.getenv("CEREBRUM_API_KEY", "")
RAG_K = int(os.getenv("RAG_K", "3"))

BASE_DIR = Path(__file__).parent.parent.parent
CHAIN_PATH = BASE_DIR / "default_chain.json"
VECTORS_PATH = BASE_DIR / "vectors.json"

CHAIN = json.loads(CHAIN_PATH.read_text(encoding="utf-8")) if CHAIN_PATH.exists() else {"blocks": [], "connections": []}
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8")) if VECTORS_PATH.exists() else {"chunks": [], "embeddings": []}


def _load_container():
    try:
        module = __import__(f"app.containers.{DOMAIN}", fromlist=["Container"])
        return getattr(module, f"{DOMAIN.title().replace('_', '')}Container", None)
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
    return {"domain": DOMAIN, "chain": CHAIN}


@router.post("/run")
async def run_chain(payload: Dict[str, Any]):
    container_cls = _load_container()
    inputs = payload.get("input", {})
    if container_cls:
        return {
            "status": "success",
            "domain": DOMAIN,
            "result": container_cls().run_chain(CHAIN, inputs),
        }
    return {"status": "error", "error": "Container not available"}


@router.post("/chat")
async def chat(payload: Dict[str, Any]):
    message = payload.get("message", "")
    history = payload.get("history", [])

    context_chunks = []
    if OLLAMA_URL and VECTORS.get("embeddings"):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                emb_resp = await client.post(
                    f"{OLLAMA_URL.rstrip('/')}/api/embeddings",
                    json={"model": OLLAMA_MODEL, "prompt": message},
                )
                emb_resp.raise_for_status()
                emb = emb_resp.json().get("embedding", [])
                if emb:
                    context_chunks = _retrieve(emb)
        except Exception as exc:
            logger.warning("Embedding for RAG failed: %s", exc)

    system = "You are a helpful assistant."
    if context_chunks:
        system += "\n\nRelevant context:\n" + "\n\n".join(context_chunks)

    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": message}]

    if OLLAMA_URL:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{OLLAMA_URL.rstrip('/')}/api/chat",
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": messages,
                        "stream": False,
                        "options": {"temperature": 0.7},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = (data.get("message") or {}).get("content", "")
                return {"text": text, "provider": "ollama", "model": OLLAMA_MODEL}
        except Exception as exc:
            logger.warning("Ollama chat failed: %s", exc)

    return {
        "text": "Chat is offline. Configure OLLAMA_URL to restore AI responses.",
        "provider": "offline_template",
        "model": "offline",
    }
