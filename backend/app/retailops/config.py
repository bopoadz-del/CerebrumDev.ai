"""RetailOps runtime configuration (env-driven, no hardcoded secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


# Embedding dimensionality. Fixed so the pgvector column and migrations are
# stable; the deterministic local embedder and fastembed default both use 384.
EMBED_DIM = 384


def _default_database_url() -> str:
    return os.getenv(
        "RETAILOPS_DATABASE_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://retailops:retailops@localhost:5432/retailops",
        ),
    )


@dataclass
class RetailOpsConfig:
    database_url: str = field(default_factory=_default_database_url)
    embed_backend: str = field(
        default_factory=lambda: os.getenv("RETAILOPS_EMBED_BACKEND", "hash")
    )
    embed_dim: int = EMBED_DIM

    # Retrieval tuning
    vector_candidates: int = field(
        default_factory=lambda: int(os.getenv("RETAILOPS_VECTOR_CANDIDATES", "20"))
    )
    lexical_candidates: int = field(
        default_factory=lambda: int(os.getenv("RETAILOPS_LEXICAL_CANDIDATES", "20"))
    )
    rrf_k: int = field(default_factory=lambda: int(os.getenv("RETAILOPS_RRF_K", "60")))
    top_k: int = field(default_factory=lambda: int(os.getenv("RETAILOPS_TOP_K", "6")))

    # LLM provider
    llm_provider: str = field(
        default_factory=lambda: os.getenv("RETAILOPS_LLM_PROVIDER", "auto")
    )

    def normalized_sqlalchemy_url(self) -> str:
        """Return a SQLAlchemy 2.x url using the psycopg (v3) driver."""
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        return url


def get_config() -> RetailOpsConfig:
    return RetailOpsConfig()
