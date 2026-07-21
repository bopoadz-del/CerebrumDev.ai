"""Embedding providers for Steward production RAG.

Default backend is deterministic feature-hash (CI / offline). Production uses
FastEmbed ONNX ``BAAI/bge-small-en-v1.5`` at 384 dims. When
``STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS`` is set, hash/fake backends are refused
(no silent fallback).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Optional

from app.steward.config import (
    EMBED_DIM,
    FASTEMBED_MODEL,
    StewardRagConfig,
    get_config,
)
from app.steward.embedding_guard import EmbeddingMismatchError, fingerprint_for_backend

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class EmbeddingProvider:
    dim: int = EMBED_DIM
    backend_name: str = "unknown"

    def embed(self, text: str) -> List[float]:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    @property
    def fingerprint(self) -> str:
        return fingerprint_for_backend(self.backend_name)


class HashEmbeddingProvider(EmbeddingProvider):
    backend_name = "hash"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = _tokenize(text)
        if not tokens:
            return vec
        for tok in tokens:
            features = [tok]
            if len(tok) > 3:
                features.extend(tok[i : i + 3] for i in range(len(tok) - 2))
            for feat in features:
                h = int.from_bytes(
                    hashlib.md5(feat.encode("utf-8")).digest()[:8], "little"
                )
                idx = h % self.dim
                sign = 1.0 if (h >> 63) & 1 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class FastEmbedProvider(EmbeddingProvider):
    backend_name = "fastembed"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=FASTEMBED_MODEL)
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        import numpy as np

        out = []
        for arr in self._model.embed(list(texts)):
            v = np.asarray(arr, dtype="float32")
            n = float(np.linalg.norm(v))
            if n > 0:
                v = v / n
            out.append(v.tolist())
        return out


_provider: Optional[EmbeddingProvider] = None


def get_embedder(config: Optional[StewardRagConfig] = None) -> EmbeddingProvider:
    global _provider
    if _provider is not None:
        return _provider
    config = config or get_config()
    if config.require_production_embeddings and config.embed_backend != "fastembed":
        raise EmbeddingMismatchError(
            "production embeddings required: set STEWARD_EMBED_BACKEND=fastembed"
        )
    if config.embed_backend == "fastembed":
        try:
            _provider = FastEmbedProvider(config.embed_dim)
        except Exception as exc:
            if config.require_production_embeddings:
                raise EmbeddingMismatchError(
                    f"fastembed unavailable and production embeddings required: {exc}"
                ) from exc
            raise EmbeddingMismatchError(
                f"fastembed requested but unavailable (no silent hash fallback): {exc}"
            ) from exc
    elif config.embed_backend == "hash":
        _provider = HashEmbeddingProvider(config.embed_dim)
    else:
        raise EmbeddingMismatchError(f"unknown STEWARD_EMBED_BACKEND={config.embed_backend!r}")
    return _provider


def reset_embedder() -> None:
    global _provider
    _provider = None
