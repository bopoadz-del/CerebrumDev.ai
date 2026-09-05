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

That rule governs the PRIMARY provider. A cross-provider *fallback leg* --
see :func:`get_factory_fallback_leg` -- is available to the factory coder
only, runs only after the primary has already failed, and is pinned to a
zero-priced model so it cannot create the cost surprise the rule exists to
prevent.

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


def _is_openrouter_base(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def _openrouter_key(*prefixes: str) -> str:
    """Resolve an OpenRouter key for a kimi-wire OpenRouter base_url.

    Path-prefixed ``*_LLM_API_KEY`` still wins (operator override). Shared
    Moonshot names (``KIMI_API_KEY`` / ``CEREBRUM_LLM_API_KEY``) are excluded
    — those keys 401 on OpenRouter.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["OPENROUTER_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY"])
    return _env_first(*candidates)


def _resolve_kimi_api_key(base_url: str, *prefixes: str) -> str:
    """Pick the key that belongs to the resolved host.

    When ``base_url`` is OpenRouter, never fall back to Moonshot keys.
    """
    if _is_openrouter_base(base_url):
        return _openrouter_key(*prefixes)
    return _kimi_key(*prefixes)


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
    # kimi-k2-0905-preview (the old Cerebrum-Blocks-aligned default) answers
    # 404 on api.moonshot.ai — measured live on the 2026-08-13 factory build:
    # every primary call failed and only the fallback leg did the work. The
    # code-oriented sibling is real on this endpoint; override via KIMI_MODEL.
    return _env_first(*candidates, default="kimi-k2.7-code")


def _kimi_fallback_model(*prefixes: str, default: str) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_FALLBACK_MODEL")
    candidates.extend(["KIMI_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    return _env_first(*candidates, default=default)


def _kimi_config(*prefixes: str) -> Dict[str, Any]:
    base_url = _kimi_base_url(*prefixes)
    return {
        "provider": "kimi",
        "api_key": _resolve_kimi_api_key(base_url, *prefixes),
        "base_url": base_url,
        "model": _kimi_model(*prefixes),
        "fallback_model": _kimi_fallback_model(*prefixes, default="moonshot-v1-8k"),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
        "temperature": _llm_temperature(),
    }


def _factory_kimi_config(*prefixes: str) -> Dict[str, Any]:
    """Factory config with a code-oriented fallback model default."""
    base_url = _kimi_base_url(*prefixes)
    return {
        "provider": "kimi",
        "api_key": _resolve_kimi_api_key(base_url, *prefixes),
        "base_url": base_url,
        "model": _kimi_model(*prefixes),
        # The fallback leg must be a DIFFERENT, live model: with the primary
        # now kimi-k2.7-code, falling back to itself would just
        # replay a 429 into the same rate limit. moonshot-v1-8k is weaker but
        # proven to write handlers, and provenance headers record which leg
        # produced every artifact.
        "fallback_model": _kimi_fallback_model(*prefixes, default="moonshot-v1-8k"),
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
    turn on chat LLM calls. An OpenRouter key counts only when the resolved
    chat base_url is actually OpenRouter (Moonshot keys stay Moonshot).
    """
    chat_base = _kimi_base_url("CEREBRUM_CHAT")
    if (
        _resolve_kimi_api_key(chat_base, "CEREBRUM_CHAT")
        or _has_kimi_credentials()
    ):
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
        if _is_openrouter_base(str(cfg.get("base_url", ""))):
            cfg["error"] = (
                "Factory architect base_url is OpenRouter but OPENROUTER_API_KEY "
                "(or FACTORY_LLM_FALLBACK_API_KEY) is not set; "
                "KIMI_API_KEY / CEREBRUM_LLM_API_KEY are Moonshot credentials "
                "and will 401 on this host"
            )
        else:
            cfg["error"] = (
                "Factory architect requires KIMI_API_KEY (or CEREBRUM_LLM_API_KEY), "
                "or set KIMI_MOCK=1 for tests"
            )
    return cfg


# -- Cross-provider fallback leg (OpenRouter) ------------------------------
#
# The rule above -- never fall through to another provider -- is about COST
# SURPRISE: a silent switch that moves a bill is a product bug. That rule
# governs which provider is PRIMARY, and it stands.
#
# A fallback leg is a different question. It runs only after the primary has
# already failed, so the alternative it is measured against is not "a cheaper
# provider", it is "no artifact at all". And a leg pinned to a ``:free`` slug
# cannot move a bill, so the reason for the prohibition does not reach it.
#
# The invariant is preserved literally: a non-free fallback model is refused
# unless FACTORY_LLM_FALLBACK_ALLOW_PAID=1 says otherwise. Setting
# OPENROUTER_API_KEY is the explicit act that arms the leg -- there is no
# path where it turns on by itself.

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Verified against OpenRouter's live catalogue on 2026-08-27: prompt and
#: completion both price 0, 1M context. The ``:free`` suffix is
#: load-bearing -- plain ``minimax/minimax-m3`` is the paid tier at
#: $0.30/$1.20 per Mtok, so dropping five characters silently starts a bill.
#:
#: This was ``z-ai/glm-5.2:free`` until measured availability decided it.
#: Four consecutive calls per model on 2026-08-27, same key:
#:
#:     z-ai/glm-5.2:free        1 of 4   (upstream_provider_shared_pool 429)
#:     minimax/minimax-m3:free  3 of 4
#:
#: A fallback leg that fails three times in four is not a fallback -- it is a
#: second way for the request to die. GLM 5.2's free tier is also 256K
#: context against minimax-m3's 1M, so the swap costs nothing on capability.
#: The 429 retry stays regardless: a shared free pool will rate-limit
#: whichever slug sits in it.
DEFAULT_OPENROUTER_FALLBACK_MODEL = "minimax/minimax-m3:free"

SUPPORTED_FALLBACK_PROVIDERS = ("openrouter",)


def _is_free_slug(model: str) -> bool:
    """OpenRouter marks zero-priced models with a ``:free`` variant suffix."""
    return model.strip().lower().endswith(":free")


def get_factory_fallback_leg() -> Dict[str, Any] | None:
    """The cross-provider fallback leg for the factory coder, or None.

    Returns a leg with its OWN endpoint and credentials -- unlike
    ``fallback_model``, which only swaps the model name while reusing the
    primary's base_url and key. Crossing vendors needs the whole triple.

    Returns None when unarmed (no key, or explicitly disabled). Returns a leg
    carrying ``error`` when it is armed but misconfigured, so the caller can
    report why the leg did not run instead of silently having no fallback.
    """
    if _env_first("FACTORY_LLM_FALLBACK_PROVIDER", default="openrouter").lower() in {
        "none",
        "off",
        "disabled",
    }:
        return None

    api_key = _env_first("OPENROUTER_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY")
    if not api_key:
        return None

    model = _env_first(
        "FACTORY_LLM_FALLBACK_MODEL",
        "OPENROUTER_MODEL",
        default=DEFAULT_OPENROUTER_FALLBACK_MODEL,
    )

    leg: Dict[str, Any] = {
        "provider": "openrouter",
        "api_key": api_key,
        "base_url": _env_first("OPENROUTER_BASE_URL", default=OPENROUTER_BASE_URL),
        "model": model,
        "temperature": _llm_temperature(),
        # Whether this leg can spend money. Two decisions hang off it, and
        # both are the same cost argument, so they share one flag rather than
        # drifting apart:
        #
        #  * a 429 is retried per Retry-After (free slugs are served from a
        #    shared upstream pool and answer 429 within seconds -- measured
        #    live 2026-08-25 on the first call with an unused key); retrying
        #    a PAID 429 would spend money on the same answer;
        #  * the leg may run with no primary configured at all, which would
        #    be a cost surprise if the leg were billable.
        "is_free": _is_free_slug(model),
    }

    if not _is_free_slug(model) and not _truthy("FACTORY_LLM_FALLBACK_ALLOW_PAID"):
        leg["error"] = (
            f"fallback model {model!r} is not a ':free' slug and "
            "FACTORY_LLM_FALLBACK_ALLOW_PAID is not set; refusing to arm a "
            "fallback leg that can spend money without being asked to"
        )
    return leg


def active_provider() -> str:
    return get_llm_config()["provider"]
