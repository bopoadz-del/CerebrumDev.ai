"""Dual RAG embeddings: feature-hash (CI/demo) or FastEmbed bge-small (pilot/prod).

When ``STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1``, hash backends are refused —
no silent fallback.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Optional, Protocol

from app.estate_kit.rag.config import (
    FASTEMBED_MODEL,
    FASTEMBED_PROVIDER_ID,
    LOCAL_FEATURE_HASH_V1,
    DualRagConfig,
    get_dual_rag_config,
)

_DIMENSIONS = 384
_MAX_CHARS = 200_000

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+(?:['-][a-zA-Z0-9]+)*", re.UNICODE)


class EmbeddingError(RuntimeError):
    """Fail-closed embedding configuration / runtime error."""


class Embedder(Protocol):
    provider_id: str

    def embed(self, text: str) -> List[float]: ...


class HashEmbedder:
    provider_id = LOCAL_FEATURE_HASH_V1

    def embed(self, text: str) -> List[float]:
        if not text:
            raise ValueError("Cannot embed empty text.")
        text = text[:_MAX_CHARS]
        vector = [0.0] * _DIMENSIONS
        for token in _TOKEN_RE.findall(text.lower()):
            token_bytes = token.encode("utf-8")
            digest = hashlib.sha256(token_bytes).digest()
            dim = int.from_bytes(digest[:4], "big") % _DIMENSIONS
            sign = 1 if digest[4] % 2 == 0 else -1
            weight = 1.0 + math.log1p(len(token))
            vector[dim] += sign * weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            raise ValueError("Embedding produced a zero vector.")
        precision = 8
        vector = [round(v / norm, precision) for v in vector]
        rounded_norm = math.sqrt(sum(v * v for v in vector))
        if rounded_norm == 0:
            raise ValueError("Embedding produced a zero vector after rounding.")
        return [round(v / rounded_norm, precision) for v in vector]


class FastEmbedEmbedder:
    provider_id = FASTEMBED_PROVIDER_ID

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=FASTEMBED_MODEL)

    def embed(self, text: str) -> List[float]:
        if not text:
            raise ValueError("Cannot embed empty text.")
        text = text[:_MAX_CHARS]
        import numpy as np

        arr = next(self._model.embed([text]))
        v = np.asarray(arr, dtype="float32")
        n = float(np.linalg.norm(v))
        if n <= 0:
            raise ValueError("Embedding produced a zero vector.")
        return (v / n).astype("float64").tolist()


_EMBEDDER: Optional[Embedder] = None


def get_embedder(config: Optional[DualRagConfig] = None) -> Embedder:
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    config = config or get_dual_rag_config()
    config.validate_or_raise()
    if config.embed_backend == "fastembed":
        try:
            _EMBEDDER = FastEmbedEmbedder()
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(
                f"fastembed unavailable and required (no silent hash fallback): {exc}"
            ) from exc
    elif config.embed_backend == "hash":
        if config.require_production_embeddings:
            raise EmbeddingError(
                "production embeddings required: set STEWARD_EMBED_BACKEND=fastembed"
            )
        _EMBEDDER = HashEmbedder()
    else:
        raise EmbeddingError(f"unknown STEWARD_EMBED_BACKEND={config.embed_backend!r}")
    return _EMBEDDER


def reset_embedder() -> None:
    global _EMBEDDER
    _EMBEDDER = None


def embed_text(text: str) -> List[float]:
    """Backward-compatible helper — uses configured embedder."""
    return get_embedder().embed(text)


def embed_texts(texts: List[str]) -> List[List[float]]:
    emb = get_embedder()
    return [emb.embed(t) for t in texts]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Vector dimension mismatch.")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# Re-export for generated-product imports that expect the constant.
__all__ = [
    "LOCAL_FEATURE_HASH_V1",
    "FASTEMBED_PROVIDER_ID",
    "EmbeddingError",
    "get_embedder",
    "reset_embedder",
    "embed_text",
    "embed_texts",
    "cosine_similarity",
]
