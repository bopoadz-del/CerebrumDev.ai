"""Unified LLM provider configuration for CerebrumDev.ai.

Two paths are configured independently:

* Chat / chain_generator path: ``get_llm_config()``
  - Preferred: ``CEREBRUM_CHAT_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``KIMI_*`` / ``ANTHROPIC_*`` or ``CEREBRUM_LLM_*``

* Factory Product Architect / platform CLI path: ``get_factory_llm_config()``
  - Preferred: ``CEREBRUM_FACTORY_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``CEREBRUM_LLM_*`` then ``KIMI_*`` / ``ANTHROPIC_*``

**Kimi and Claude are both supported. Kimi is the DEFAULT.**

Claude is an addition, not a replacement: it exists so the factory keeps
running when Kimi credits are out, and so the two can be compared on one
blueprint. Selection is deliberate, never accidental --
:func:`_detect_provider` resolves to Kimi whenever Kimi credentials are
present, *even if Claude credentials are also present*, so nobody's bill
changes by having a second key in the environment. Claude is used only when
``LLM_PROVIDER=claude`` is set explicitly, or when Kimi has no credentials and
Claude does.

Selecting a provider whose key is missing is a loud error. It never falls
through to the other provider -- a silent switch is a cost surprise, which is
a product bug.

``LLM_PROVIDER`` accepts ``kimi``/``moonshot`` (aliased to kimi) and
``claude``/``anthropic`` (aliased to claude).
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


def _claude_key(*prefixes: str) -> str:
    """Resolve a Claude API key. Mirrors _kimi_key's prefix-then-shared order.

    ``CEREBRUM_LLM_API_KEY`` is shared with Kimi deliberately: it is the
    provider-agnostic name already in use, and which provider consumes it is
    decided by LLM_PROVIDER, not by the variable.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "CEREBRUM_LLM_API_KEY"])
    return _env_first(*candidates)


def _claude_base_url(*prefixes: str) -> str:
    candidates: List[str] = [f"{prefix}_LLM_BASE_URL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_BASE_URL", "CEREBRUM_LLM_BASE_URL"])
    return _env_first(*candidates, default="https://api.anthropic.com/v1")


def _claude_model(*prefixes: str) -> str:
    candidates: List[str] = [f"{prefix}_LLM_MODEL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_MODEL", "CLAUDE_MODEL", "CEREBRUM_LLM_MODEL"])
    return _env_first(*candidates, default="claude-sonnet-4-5")


def _claude_fallback_model(*prefixes: str, default: str) -> str:
    candidates: List[str] = [f"{prefix}_LLM_FALLBACK_MODEL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    return _env_first(*candidates, default=default)


def _claude_config(*prefixes: str) -> Dict[str, Any]:
    return {
        "provider": "claude",
        "api_key": _claude_key(*prefixes),
        "base_url": _claude_base_url(*prefixes),
        "model": _claude_model(*prefixes),
        "fallback_model": _claude_fallback_model(*prefixes, default="claude-haiku-4-5-20251001"),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("CLAUDE_MOCK"),
        "temperature": _llm_temperature(),
    }


def _factory_claude_config(*prefixes: str) -> Dict[str, Any]:
    """Factory config. Same shape as _factory_kimi_config, different provider."""
    cfg = _claude_config(*prefixes)
    cfg["fallback_model"] = _claude_fallback_model(
        *prefixes, default="claude-haiku-4-5-20251001"
    )
    return cfg


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


def normalise_provider(name: str) -> str:
    """``moonshot`` -> ``kimi``, ``anthropic`` -> ``claude``. Others pass through."""
    name = (name or "").strip().lower()
    if name == "moonshot":
        return "kimi"
    if name == "anthropic":
        return "claude"
    return name


SUPPORTED_PROVIDERS = ("kimi", "claude")


def _has_kimi_credentials() -> bool:
    """A Kimi-specific key, or the shared key with no Claude-specific key.

    The shared ``CEREBRUM_LLM_API_KEY`` is ambiguous by design. It counts as
    Kimi unless the environment says otherwise, which keeps every existing
    single-key deployment on Kimi exactly as before.
    """
    if _env_first("CEREBRUM_CHAT_LLM_API_KEY", "CEREBRUM_FACTORY_LLM_API_KEY", "KIMI_API_KEY"):
        return True
    shared = _env_first("CEREBRUM_LLM_API_KEY")
    if not shared:
        return False
    # A shared key alongside an explicit Anthropic key belongs to Kimi only if
    # no Anthropic-specific key is set; otherwise it is genuinely ambiguous and
    # we still prefer Kimi (see _detect_provider) -- this only decides whether
    # Kimi credentials are considered *present at all*.
    return True


def _has_claude_credentials() -> bool:
    return bool(_env_first("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"))


def _detect_provider() -> str:
    """Auto-detect provider from configured credentials.

    Kimi wins whenever Kimi credentials are present, even if Claude
    credentials are also present. Adding an ANTHROPIC_API_KEY to an existing
    deployment must not move that deployment's traffic -- or its bill -- onto
    a different provider. Claude is auto-selected only when it is the only
    provider configured; otherwise it must be asked for by name.

    Prefers the chat-scoped key so a factory-only key does not accidentally
    turn on chat LLM calls.
    """
    if _kimi_key("CEREBRUM_CHAT") or _has_kimi_credentials():
        return "kimi"
    if _has_claude_credentials():
        return "claude"
    return ""


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM config for the chat/chain_generator path."""
    provider = normalise_provider(os.getenv("LLM_PROVIDER", "")) or _detect_provider()

    if provider in SUPPORTED_PROVIDERS:
        cfg = (
            _kimi_config("CEREBRUM_CHAT")
            if provider == "kimi"
            else _claude_config("CEREBRUM_CHAT")
        )
        # Explicit provider with only the mock flag and no key → inactive for
        # kit chat (stay offline). Factory path uses get_factory_llm_config.
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
    """Factory Product Architect — Kimi (default) or Claude, + mock for tests.

    Fails closed per provider: asking for a provider whose key is absent is an
    error carrying that provider's name. It never silently borrows the other
    provider's credentials.
    """
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    explicit = normalise_provider(raw)
    if raw and explicit not in SUPPORTED_PROVIDERS:
        return {
            "provider": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "mock": False,
            "error": (
                f"Factory architect supports {' and '.join(SUPPORTED_PROVIDERS)} "
                f"(kimi is the default); LLM_PROVIDER={raw} is not allowed for "
                "product architecture"
            ),
        }

    provider = explicit or _detect_provider() or "kimi"

    if provider == "claude":
        cfg = _factory_claude_config("CEREBRUM_FACTORY")
        if cfg["mock"]:
            return cfg
        if not cfg["api_key"]:
            cfg["error"] = (
                "Factory architect was asked for Claude but ANTHROPIC_API_KEY "
                "(or CEREBRUM_LLM_API_KEY) is not set; refusing to fall back to "
                "another provider — set the key or unset LLM_PROVIDER"
            )
        return cfg

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
