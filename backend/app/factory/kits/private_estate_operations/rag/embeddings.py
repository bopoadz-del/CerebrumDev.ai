"""Deterministic local feature-hash embeddings for estate RAG (384-d cosine)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import List

LOCAL_FEATURE_HASH_V1 = "local_feature_hash_v1"
_DIMENSIONS = 384
_MAX_CHARS = 200_000

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+(?:['-][a-zA-Z0-9]+)*", re.UNICODE)


def embed_text(text: str) -> List[float]:
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


def embed_texts(texts: List[str]) -> List[List[float]]:
    return [embed_text(text) for text in texts]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Vector dimension mismatch.")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
