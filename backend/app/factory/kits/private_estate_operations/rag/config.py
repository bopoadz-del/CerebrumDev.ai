"""Env-driven dual RAG config (persistence + embeddings). No secrets in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_FEATURE_HASH_V1 = "local_feature_hash_v1"
FASTEMBED_PROVIDER_ID = f"fastembed:{FASTEMBED_MODEL}"
HASH_FINGERPRINT = f"hash:{LOCAL_FEATURE_HASH_V1}:384"
FASTEMBED_FINGERPRINT = f"fastembed:{FASTEMBED_MODEL}:384"


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def resolve_database_url() -> str:
    return (
        os.getenv("STEWARD_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


@dataclass
class DualRagConfig:
    """Controls dual RAG store + embedder for generated Steward products."""

    database_url: str = field(default_factory=resolve_database_url)
    embed_backend: str = field(
        default_factory=lambda: os.getenv("STEWARD_EMBED_BACKEND", "hash").strip().lower()
    )
    require_production_embeddings: bool = field(
        default_factory=lambda: _truthy("STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS")
    )
    require_persistent_rag: bool = field(
        default_factory=lambda: _truthy("STEWARD_REQUIRE_PERSISTENT_RAG")
    )
    # auto | postgres | jsonl
    persistence: str = field(
        default_factory=lambda: os.getenv("STEWARD_RAG_PERSISTENCE", "auto").strip().lower()
    )
    # Absolute cosine floor for FastEmbed (semantic models score unrelated
    # queries ~0.5–0.58; real fixture hits land ≥~0.70). Hash stays at caller min.
    semantic_score_floor: float = field(
        default_factory=lambda: float(os.getenv("STEWARD_RAG_SEMANTIC_FLOOR", "0.62"))
    )

    def normalized_psycopg_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql+psycopg://"):
            return "postgresql://" + url[len("postgresql+psycopg://") :]
        if url.startswith("postgres://"):
            return "postgresql://" + url[len("postgres://") :]
        return url

    def use_postgres(self) -> bool:
        mode = self.persistence
        if mode == "jsonl":
            return False
        if mode == "postgres":
            return True
        # auto: Postgres when a URL is present
        return bool(self.database_url)

    def embedding_provider_id(self) -> str:
        if self.embed_backend == "fastembed":
            return FASTEMBED_PROVIDER_ID
        return LOCAL_FEATURE_HASH_V1

    def fingerprint(self) -> str:
        if self.embed_backend == "fastembed":
            return FASTEMBED_FINGERPRINT
        return HASH_FINGERPRINT

    def effective_min_score(self, requested: float) -> float:
        """Raise the relevance floor for semantic backends so unknowns fail closed."""
        if self.embed_backend == "fastembed":
            return max(float(requested), float(self.semantic_score_floor))
        return float(requested)

    def validate_or_raise(self) -> None:
        if self.require_persistent_rag and not self.use_postgres():
            raise RuntimeError(
                "persistent RAG required: set STEWARD_DATABASE_URL (or DATABASE_URL) "
                "and STEWARD_RAG_PERSISTENCE=postgres|auto"
            )
        if self.require_production_embeddings and self.embed_backend != "fastembed":
            raise RuntimeError(
                "production embeddings required: set STEWARD_EMBED_BACKEND=fastembed"
            )
        if self.use_postgres() and not self.database_url:
            raise RuntimeError(
                "STEWARD_RAG_PERSISTENCE=postgres but STEWARD_DATABASE_URL/DATABASE_URL is missing"
            )


def get_dual_rag_config() -> DualRagConfig:
    return DualRagConfig()
