"""Block taxonomy for CerebrumDev.ai.

The platform always includes a set of core/built-in blocks. Users can optionally
add Fork primitive blocks on top of the domain kit.
"""

from typing import List

BUILTIN_BLOCKS: List[str] = [
    # Runtime / execution
    "orchestrator",
    "smart_orchestrator",
    "async_processor",
    "validation_pipeline",
    # Storage / caching
    "local_drive",
    "storage",
    "cache_manager",
    "memory",
    # RAG / search
    "vector_search",
    "zvec",
    "search",
    "knowledge",
    # LLM / AI
    "llm_enhancer",
    "learning_engine",
    "recommendation_template",
    # Document / vision parsing
    "pdf",
    "ocr",
    "ocr_v2",
    "image",
    "document_engine",
    # Interface / security / monitoring
    "chat",
    "auth",
    "secrets",
    "health_check",
    "monitoring",
    "error_tracking",
]

OPTIONAL_BLOCKS: List[str] = [
    "voice",
    "google_drive",
    "onedrive",
    "android_drive",
    "email",
    "webhook",
    "web",
    "translate",
    "code",
    "capture",
    "jetson_gateway",
    "legal_v2",
    "finance_v2",
    "retail_v2",
]


def is_builtin(block_name: str) -> bool:
    return block_name in BUILTIN_BLOCKS


def is_optional(block_name: str) -> bool:
    return block_name in OPTIONAL_BLOCKS


def list_optional_blocks() -> List[str]:
    return list(OPTIONAL_BLOCKS)
