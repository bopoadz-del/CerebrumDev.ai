"""Embedding fingerprint / mismatch guard — fail closed on drift."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.steward.config import (
    EMBEDDING_FINGERPRINT_V1,
    HASH_FINGERPRINT_V1,
    StewardRagConfig,
    get_config,
)


class EmbeddingMismatchError(RuntimeError):
    """Raised when stored corpus fingerprint disagrees with the active embedder."""


@dataclass(frozen=True)
class EmbeddingGuardReport:
    ok: bool
    expected: str
    observed: Optional[str]
    detail: str


def assert_fingerprint_compatible(
    stored_fingerprint: Optional[str],
    *,
    config: Optional[StewardRagConfig] = None,
) -> EmbeddingGuardReport:
    config = config or get_config()
    expected = config.expected_fingerprint()
    if config.require_production_embeddings and expected != EMBEDDING_FINGERPRINT_V1:
        raise EmbeddingMismatchError(
            "STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS is set but embed backend is not fastembed"
        )
    if stored_fingerprint is None:
        return EmbeddingGuardReport(True, expected, None, "no prior fingerprint")
    if stored_fingerprint != expected:
        raise EmbeddingMismatchError(
            f"embedding fingerprint mismatch: stored={stored_fingerprint!r} "
            f"expected={expected!r}"
        )
    return EmbeddingGuardReport(True, expected, stored_fingerprint, "match")


def fingerprint_for_backend(backend: str) -> str:
    if backend == "fastembed":
        return EMBEDDING_FINGERPRINT_V1
    if backend == "hash":
        return HASH_FINGERPRINT_V1
    raise ValueError(f"unknown embed backend: {backend}")
