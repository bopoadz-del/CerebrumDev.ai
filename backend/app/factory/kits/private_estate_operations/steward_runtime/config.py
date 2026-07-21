"""Steward production RAG configuration (env-driven, no secrets in code)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

EMBED_DIM = 384
FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_FINGERPRINT_V1 = "fastembed:BAAI/bge-small-en-v1.5:384"
HASH_FINGERPRINT_V1 = "hash:local_feature_hash_v1:384"

# Canonical platform Layer-1 scope
PLATFORM_TENANT_ID = "platform"
PLATFORM_PROJECT_ID = "prebuilt_steward_core"


def _default_database_url() -> str:
    return os.getenv(
        "STEWARD_DATABASE_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://steward:steward@localhost:5432/steward",
        ),
    )


@dataclass
class StewardRagConfig:
    database_url: str = field(default_factory=_default_database_url)
    # hash = deterministic CI/demo; fastembed = production ONNX
    embed_backend: str = field(
        default_factory=lambda: os.getenv("STEWARD_EMBED_BACKEND", "hash").strip().lower()
    )
    # When true, refuse hash/fake backends (production fail-closed).
    require_production_embeddings: bool = field(
        default_factory=lambda: os.getenv("STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS", "0")
        in {"1", "true", "yes", "on"}
    )
    embed_dim: int = EMBED_DIM
    vector_candidates: int = field(
        default_factory=lambda: int(os.getenv("STEWARD_VECTOR_CANDIDATES", "20"))
    )
    lexical_candidates: int = field(
        default_factory=lambda: int(os.getenv("STEWARD_LEXICAL_CANDIDATES", "20"))
    )
    rrf_k: int = field(default_factory=lambda: int(os.getenv("STEWARD_RRF_K", "60")))
    top_k: int = field(default_factory=lambda: int(os.getenv("STEWARD_TOP_K", "6")))

    def normalized_sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        return url

    def expected_fingerprint(self) -> str:
        if self.embed_backend == "fastembed":
            return EMBEDDING_FINGERPRINT_V1
        return HASH_FINGERPRINT_V1


def get_config() -> StewardRagConfig:
    return StewardRagConfig()
