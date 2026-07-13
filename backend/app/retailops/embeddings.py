"""Embedding providers for RetailOps.

Default backend is a deterministic feature-hash embedder: fast, dependency-free,
and reproducible — ideal for automated tests and offline pilots. An optional
``fastembed`` backend (ONNX BGE-small) is used when ``RETAILOPS_EMBED_BACKEND=fastembed``.
Both produce L2-normalized ``EMBED_DIM`` vectors so cosine distance is stable.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Optional

from app.retailops.config import EMBED_DIM, RetailOpsConfig, get_config

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class EmbeddingProvider:
    dim: int = EMBED_DIM

    def embed(self, text: str) -> List[float]:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic feature-hashing embedder with sub-word n-grams."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = _tokenize(text)
        if not tokens:
            return vec
        for tok in tokens:
            features = [tok]
            # character trigrams give the hash embedder some fuzziness.
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


class FastEmbedProvider(EmbeddingProvider):  # pragma: no cover - optional heavy dep
    """ONNX BGE-small embedder via fastembed (optional)."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
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


def get_embedder(config: Optional[RetailOpsConfig] = None) -> EmbeddingProvider:
    global _provider
    if _provider is not None:
        return _provider
    config = config or get_config()
    if config.embed_backend == "fastembed":
        try:
            _provider = FastEmbedProvider(config.embed_dim)
        except Exception:
            _provider = HashEmbeddingProvider(config.embed_dim)
    else:
        _provider = HashEmbeddingProvider(config.embed_dim)
    return _provider


def reset_embedder() -> None:
    global _provider
    _provider = None
