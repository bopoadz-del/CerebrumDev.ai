"""Unified LLM provider configuration for CerebrumDev.ai.

Factory Product Architect (this milestone): **Kimi only** via
``get_factory_llm_config()`` — refuses Ollama/Qwen.

Kit-chain chat still uses ``get_llm_config()`` with legacy providers when
explicitly configured.

Preferred Factory env:
  KIMI_API_KEY / KIMI_BASE_URL / KIMI_MODEL

Aliases:
  CEREBRUM_LLM_API_KEY / CEREBRUM_LLM_BASE_URL / CEREBRUM_LLM_MODEL
  LLM_PROVIDER=kimi|moonshot
"""

from __future__ import annotations

import os
from typing import Any, Dict


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _kimi_key() -> str:
    return (
        os.getenv("KIMI_API_KEY", "").strip()
        or os.getenv("CEREBRUM_LLM_API_KEY", "").strip()
    )


def _kimi_base_url() -> str:
    return (
        os.getenv("KIMI_BASE_URL", "").strip()
        or os.getenv("CEREBRUM_LLM_BASE_URL", "").strip()
        or "https://api.moonshot.cn/v1"
    )


def _kimi_model() -> str:
    return (
        os.getenv("KIMI_MODEL", "").strip()
        or os.getenv("CEREBRUM_LLM_MODEL", "").strip()
        or "moonshot-v1-8k"
    )


def _kimi_config() -> Dict[str, Any]:
    return {
        "provider": "kimi",
        "api_key": _kimi_key(),
        "base_url": _kimi_base_url(),
        "model": _kimi_model(),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
    }


def _detect_provider() -> str:
    """Auto-detect provider from configured credentials."""
    # Prefer Kimi when its key/mock is present (Factory milestone)
    if _kimi_key() or _truthy("KIMI_MOCK") or _truthy("CEREBRUM_LLM_MOCK"):
        return "kimi"
    if os.getenv("QWEN_API_KEY"):
        return "qwen"
    if os.getenv("OLLAMA_URL"):
        return "ollama"
    return ""


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM provider, model, base URL, and API key."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower() or _detect_provider()

    if provider == "moonshot":
        provider = "kimi"

    if provider == "kimi":
        return _kimi_config()

    if provider == "qwen":
        return {
            "provider": "qwen",
            "api_key": os.getenv("QWEN_API_KEY", ""),
            "base_url": os.getenv(
                "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "model": os.getenv("QWEN_MODEL", "qwen-plus"),
            "mock": False,
        }

    if provider == "ollama":
        return {
            "provider": "ollama",
            "api_key": os.getenv("OLLAMA_API_KEY", ""),
            "base_url": os.getenv("OLLAMA_URL", ""),
            "model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
            "mock": False,
        }

    return {"provider": "", "api_key": "", "base_url": "", "model": "", "mock": False}


def get_factory_llm_config() -> Dict[str, Any]:
    """Factory Product Architect — Kimi only (+ mock for tests).

    Refuses Ollama/Qwen for the Store Manager Loop milestone.
    """
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in {"ollama", "qwen"}:
        return {
            "provider": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "mock": False,
            "error": (
                f"Factory architect is Kimi-only this milestone; "
                f"LLM_PROVIDER={explicit} is not allowed for product architecture"
            ),
        }

    cfg = _kimi_config()
    if cfg["mock"]:
        return cfg
    if not cfg["api_key"]:
        cfg["error"] = (
            "Factory architect requires KIMI_API_KEY (or CEREBRUM_LLM_API_KEY), "
            "or set KIMI_MOCK=1 for tests"
        )
    return cfg


def active_provider() -> str:
    return get_llm_config()["provider"]
