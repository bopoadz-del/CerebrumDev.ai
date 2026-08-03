"""Unified LLM provider configuration for CerebrumDev.ai.

Two Kimi paths are supported independently:

* Chat / chain_generator path: ``get_llm_config()``
  - Preferred: ``CEREBRUM_CHAT_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``KIMI_*`` or ``CEREBRUM_LLM_*``

* Factory Product Architect / platform CLI path: ``get_factory_llm_config()``
  - Preferred: ``CEREBRUM_FACTORY_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``CEREBRUM_LLM_*`` then ``KIMI_*``

Both paths are Kimi-only.
Legacy ``LLM_PROVIDER`` still works for chat (kimi/moonshot only).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _kimi_key(*prefixes: str) -> str:
    """Resolve a Kimi API key from prefixed env vars, then shared fallbacks."""
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_API_KEY")
    candidates.extend(["KIMI_API_KEY", "CEREBRUM_LLM_API_KEY"])
    return _env_first(*candidates)


def _kimi_base_url(*prefixes: str) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_BASE_URL")
    candidates.extend(["KIMI_BASE_URL", "CEREBRUM_LLM_BASE_URL"])
    return _env_first(*candidates, default="https://api.moonshot.ai/v1")


def _kimi_model(*prefixes: str) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_MODEL")
    candidates.extend(["KIMI_MODEL", "CEREBRUM_LLM_MODEL"])
    # Aligned with Cerebrum-Blocks on the K2 model; overridable via KIMI_MODEL.
    return _env_first(*candidates, default="kimi-k2-0905-preview")


def _kimi_fallback_model(*prefixes: str, default: str) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_FALLBACK_MODEL")
    candidates.extend(["KIMI_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    return _env_first(*candidates, default=default)


def _kimi_config(*prefixes: str) -> Dict[str, Any]:
    return {
        "provider": "kimi",
        "api_key": _kimi_key(*prefixes),
        "base_url": _kimi_base_url(*prefixes),
        "model": _kimi_model(*prefixes),
        "fallback_model": _kimi_fallback_model(*prefixes, default="moonshot-v1-8k"),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
        "temperature": _llm_temperature(),
    }


def _factory_kimi_config(*prefixes: str) -> Dict[str, Any]:
    """Factory config with a code-oriented fallback model default."""
    return {
        "provider": "kimi",
        "api_key": _kimi_key(*prefixes),
        "base_url": _kimi_base_url(*prefixes),
        "model": _kimi_model(*prefixes),
        "fallback_model": _kimi_fallback_model(*prefixes, default="kimi-k2.5-code"),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
        "temperature": _llm_temperature(),
    }


def _llm_temperature() -> float | None:
    """Optional temperature override (LLM_TEMPERATURE).

    None means: do not send a temperature at all — the provider applies the
    model default. Required for reasoning models (kimi-k2.x) which reject
    any explicit temperature other than 1.
    """
    raw = os.getenv("LLM_TEMPERATURE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _detect_provider() -> str:
    """Auto-detect provider from configured credentials.

    Prefers the chat-scoped key so a factory-only key does not accidentally
    turn on chat LLM calls. Kimi is the only supported provider.
    """
    if _kimi_key("CEREBRUM_CHAT") or _kimi_key():
        return "kimi"
    return ""


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM config for the chat/chain_generator path."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower() or _detect_provider()

    if provider == "moonshot":
        provider = "kimi"

    if provider == "kimi":
        cfg = _kimi_config("CEREBRUM_CHAT")
        # Explicit LLM_PROVIDER=kimi with only mock flag and no key → inactive
        # for kit chat (stay offline). Factory path uses get_factory_llm_config.
        if cfg["mock"] and not cfg["api_key"]:
            return {
                "provider": "",
                "api_key": "",
                "base_url": "",
                "model": "",
                "mock": True,
            }
        return cfg

    return {
        "provider": "",
        "api_key": "",
        "base_url": "",
        "model": "",
        # Preserve mock intent for callers even when no live provider is selected
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
    }


def get_factory_llm_config() -> Dict[str, Any]:
    """Factory Product Architect — Kimi only (+ mock for tests)."""
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit and explicit not in {"kimi", "moonshot"}:
        return {
            "provider": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "mock": False,
            "error": (
                "Factory architect is Kimi-only; "
                f"LLM_PROVIDER={explicit} is not allowed for product architecture"
            ),
        }

    cfg = _factory_kimi_config("CEREBRUM_FACTORY")
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
