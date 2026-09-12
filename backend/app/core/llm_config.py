"""Unified LLM provider configuration for CerebrumDev.ai.

Two paths are configured independently:

* Chat / chain_generator path: ``get_llm_config()``
  - Preferred: ``CEREBRUM_CHAT_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: leftover ``KIMI_*`` / ``ANTHROPIC_*`` or ``CEREBRUM_LLM_*``
  - OpenRouter is never the primary host when a native Moonshot, Claude,
    or Cursor key is present. A leftover ``CEREBRUM_LLM_BASE_URL=openrouter.ai``
    plus a dead ``OPENROUTER_API_KEY`` used to hijack Floor suggestions and
    401 while ``CEREBRUM_LLM_API_KEY`` was healthy.

* Factory Product Architect / platform CLI path: ``get_factory_llm_config()``
  - Preferred: ``CEREBRUM_FACTORY_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``CEREBRUM_LLM_*`` then leftover ``KIMI_*`` / ``ANTHROPIC_*``
  - Same OpenRouter-is-fallback-only rule as chat. The coder's optional
    cross-provider leg remains ``get_factory_fallback_leg()``.

``LLM_PROVIDER`` accepts ``cursor``, ``kimi``/``moonshot`` (aliased to kimi)
and ``claude``/``anthropic`` (aliased to claude).

``LLM_PROVIDER=cursor`` is intentional (render.yaml pins it). Cursor keys
(``CURSOR_API_KEY`` / ``CURSOR_AGENT_API_KEY`` / ``FACTORY_CURSOR_API_KEY``,
the tuple in ``cursor_ba.CURSOR_KEY_ENVS``) are the matching credential
family: they arm Background Agents (cli-pivot) and Floor HTTP chat. Chat
posts OpenAI-shaped ``/chat/completions`` to ``https://api.cursor.com/v1``.
It must not fall through to an empty provider or the Floor starter-chain
mock. OpenRouter is fallback only after that primary fails.

Claude remains an opt-in HTTP provider. Leftover Kimi/Moonshot credentials
still resolve when ``LLM_PROVIDER`` is unset or ``kimi``/``moonshot``.
Selection is deliberate, never accidental -- :func:`_detect_provider`
resolves to Kimi whenever leftover Kimi credentials are present, *even if
Claude credentials are also present*, so nobody's bill changes by having a
second key in the environment. Claude is used only when
``LLM_PROVIDER=claude`` is set explicitly, or when Kimi has no credentials
and Claude does.

Selecting a provider whose key is missing is a loud error. It never falls
through to the other provider -- a silent switch is a cost surprise, which is
a product bug.

That rule governs the PRIMARY provider. A cross-provider *fallback leg* --
see :func:`get_factory_fallback_leg` -- is available to the factory coder
only, runs only after the primary has already failed, and is pinned to a
zero-priced model so it cannot create the cost surprise the rule exists to
prevent.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

#: Cursor BA + Floor chat key names. Any one arms cli-pivot and HTTP chat;
#: first present wins.
CURSOR_KEY_ENVS = (
    "CURSOR_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "FACTORY_CURSOR_API_KEY",
)


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


def _looks_like_openrouter_key(key: str) -> bool:
    """OpenRouter issues ``sk-or-`` keys. Moonshot keys never use that prefix."""
    return (key or "").strip().lower().startswith("sk-or-")


def _looks_like_openrouter_model(model: str) -> bool:
    """OpenRouter slugs are ``org/model`` and often end in ``:free``.

    Moonshot / Claude ids are bare (``kimi-k2.7-code``, ``claude-sonnet-4-5``).
    """
    slug = (model or "").strip().lower()
    return "/" in slug or ":free" in slug


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


def _env_first_skipping(*names: str, default: str = "", reject=None) -> str:
    """Like ``_env_first`` but skip values ``reject(value)`` says are unusable."""
    skip = reject or (lambda _value: False)
    for name in names:
        value = os.getenv(name, "").strip()
        if value and not skip(value):
            return value
    return default


def _native_moonshot_key(*prefixes: str) -> str:
    """Moonshot credentials only — never an ``sk-or-`` OpenRouter key.

    Path-prefixed ``*_LLM_API_KEY`` still wins when it is a real Moonshot
    key. An OpenRouter key stuffed into ``CEREBRUM_LLM_API_KEY`` does not
    count as a native primary; OpenRouter stays the fallback leg.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["KIMI_API_KEY", "CEREBRUM_LLM_API_KEY"])
    for name in candidates:
        value = os.getenv(name, "").strip()
        if value and not _looks_like_openrouter_key(value):
            return value
    return ""


def _kimi_base_url(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_BASE_URL")
    candidates.extend(["KIMI_BASE_URL", "CEREBRUM_LLM_BASE_URL"])
    reject = _is_openrouter_base if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default="https://api.moonshot.ai/v1", reject=reject
    )


def _kimi_model(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_MODEL")
    candidates.extend(["KIMI_MODEL", "CEREBRUM_LLM_MODEL"])
    # kimi-k2-0905-preview (the old Cerebrum-Blocks-aligned default) answers
    # 404 on api.moonshot.ai — measured live on the 2026-08-13 factory build:
    # every primary call failed and only the fallback leg did the work. The
    # code-oriented sibling is real on this endpoint; override via KIMI_MODEL.
    reject = _looks_like_openrouter_model if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default="kimi-k2.7-code", reject=reject
    )


def _kimi_fallback_model(*prefixes: str, default: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_FALLBACK_MODEL")
    candidates.extend(["KIMI_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    reject = _looks_like_openrouter_model if skip_openrouter else None
    return _env_first_skipping(*candidates, default=default, reject=reject)


def _resolve_kimi_primary(*prefixes: str) -> Dict[str, str]:
    """Pick the Kimi primary endpoint.

    A native Moonshot key always owns the host and model. Pointing
    ``CEREBRUM_LLM_BASE_URL`` at OpenRouter used to send Floor chat through
    ``OPENROUTER_API_KEY`` and 401 while the Moonshot key sat unused.
    OpenRouter-only deployments (no Moonshot key) still resolve to
    OpenRouter via ``_resolve_kimi_api_key``.
    """
    native = _native_moonshot_key(*prefixes)
    if native:
        return {
            "base_url": _kimi_base_url(*prefixes, skip_openrouter=True),
            "api_key": native,
            "model": _kimi_model(*prefixes, skip_openrouter=True),
            "fallback_model": _kimi_fallback_model(
                *prefixes, default="moonshot-v1-8k", skip_openrouter=True
            ),
        }
    base_url = _kimi_base_url(*prefixes)
    return {
        "base_url": base_url,
        "api_key": _resolve_kimi_api_key(base_url, *prefixes),
        "model": _kimi_model(*prefixes),
        "fallback_model": _kimi_fallback_model(*prefixes, default="moonshot-v1-8k"),
    }


def _kimi_config(*prefixes: str) -> Dict[str, Any]:
    endpoint = _resolve_kimi_primary(*prefixes)
    return {
        "provider": "kimi",
        "api_key": endpoint["api_key"],
        "base_url": endpoint["base_url"],
        "model": endpoint["model"],
        "fallback_model": endpoint["fallback_model"],
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
        "temperature": _llm_temperature(),
    }


def _factory_kimi_config(*prefixes: str) -> Dict[str, Any]:
    """Factory config with a code-oriented fallback model default."""
    # Same primary-resolution as chat: Moonshot key wins the host so a
    # leftover OpenRouter base_url cannot steal architect / Floor chat.
    # fallback_model default stays moonshot-v1-8k — a different live
    # model from kimi-k2.7-code so a 429 is not replayed into itself.
    return _kimi_config(*prefixes)


def _claude_key(*prefixes: str) -> str:
    """Resolve a Claude API key. Mirrors _kimi_key's prefix-then-shared order.

    ``CEREBRUM_LLM_API_KEY`` is shared with Kimi deliberately: it is the
    provider-agnostic name already in use, and which provider consumes it is
    decided by LLM_PROVIDER, not by the variable.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "CEREBRUM_LLM_API_KEY"])
    return _env_first(*candidates)


def _claude_base_url(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = [f"{prefix}_LLM_BASE_URL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_BASE_URL", "CEREBRUM_LLM_BASE_URL"])
    reject = _is_openrouter_base if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default="https://api.anthropic.com/v1", reject=reject
    )


def _claude_model(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = [f"{prefix}_LLM_MODEL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_MODEL", "CLAUDE_MODEL", "CEREBRUM_LLM_MODEL"])
    reject = _looks_like_openrouter_model if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default="claude-sonnet-4-5", reject=reject
    )


def _claude_fallback_model(*prefixes: str, default: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = [f"{prefix}_LLM_FALLBACK_MODEL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    reject = _looks_like_openrouter_model if skip_openrouter else None
    return _env_first_skipping(*candidates, default=default, reject=reject)


def _native_claude_key(*prefixes: str) -> str:
    """Anthropic-issued keys only — skip ``sk-or-`` and leave shared Moonshot keys alone."""
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"])
    for name in candidates:
        value = os.getenv(name, "").strip()
        if value and not _looks_like_openrouter_key(value):
            return value
    return ""


def _claude_config(*prefixes: str) -> Dict[str, Any]:
    native = _native_claude_key(*prefixes)
    skip = bool(native)
    api_key = native or _claude_key(*prefixes)
    return {
        "provider": "claude",
        "api_key": api_key,
        "base_url": _claude_base_url(*prefixes, skip_openrouter=skip),
        "model": _claude_model(*prefixes, skip_openrouter=skip),
        "fallback_model": _claude_fallback_model(
            *prefixes, default="claude-haiku-4-5-20251001", skip_openrouter=skip
        ),
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


SUPPORTED_PROVIDERS = ("cursor", "kimi", "claude")


def _cursor_key_envs() -> tuple[str, ...]:
    """Reuse the Factory BA key names — do not fork a second list."""
    from app.factory.build.cursor_ba import CURSOR_KEY_ENVS

    return CURSOR_KEY_ENVS


def _cursor_api_base() -> str:
    from app.factory.build.cursor_ba import CURSOR_API_BASE

    return CURSOR_API_BASE.rstrip("/")


def _cursor_key(*prefixes: str) -> str:
    """Resolve a Cursor-family API key. Same naming rule as Kimi / Claude."""
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(_cursor_key_envs())
    return _env_first(*candidates)


def _cursor_base_url(*prefixes: str) -> str:
    """OpenAI-compatible path on the documented Cursor API host.

    Cloud Agents (``/v0/agents``, ``/v1/agents``) is not a chat-completions
    API. Floor still posts OpenAI-shaped ``/chat/completions`` to
    ``https://api.cursor.com/v1`` with the Cursor-family Bearer key — the
    documented public host + the key family ``LLM_PROVIDER=cursor`` names.
    OpenRouter must not steal this primary. Override via ``CURSOR_BASE_URL``
    / path-prefixed ``*_LLM_BASE_URL`` when those are not OpenRouter.
    """
    candidates: List[str] = [f"{prefix}_LLM_BASE_URL" for prefix in prefixes]
    candidates.extend(["CURSOR_BASE_URL", "CURSOR_LLM_BASE_URL"])
    override = _env_first_skipping(
        *candidates, default="", reject=_is_openrouter_base
    )
    if override:
        return override
    return f"{_cursor_api_base()}/v1"


def _looks_like_moonshot_model(model: str) -> bool:
    slug = (model or "").strip().lower()
    return slug.startswith("kimi-") or slug.startswith("moonshot-")


def _cursor_model(*prefixes: str) -> str:
    candidates: List[str] = [f"{prefix}_LLM_MODEL" for prefix in prefixes]
    candidates.extend(["CURSOR_MODEL", "CURSOR_LLM_MODEL"])

    def _reject(value: str) -> bool:
        return _looks_like_openrouter_model(value) or _looks_like_moonshot_model(value)

    return _env_first_skipping(*candidates, default="auto", reject=_reject)


def _cursor_config(*prefixes: str) -> Dict[str, Any]:
    return {
        "provider": "cursor",
        "api_key": _cursor_key(*prefixes),
        "base_url": _cursor_base_url(*prefixes),
        "model": _cursor_model(*prefixes),
        "fallback_model": "",
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("CURSOR_MOCK"),
        "temperature": _llm_temperature(),
    }


def _has_cursor_credentials() -> bool:
    return bool(_cursor_key())


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
    if _has_cursor_credentials():
        return "cursor"
    return ""


def _config_for_provider(provider: str, *prefixes: str) -> Dict[str, Any]:
    if provider == "kimi":
        return _kimi_config(*prefixes)
    if provider == "claude":
        return _claude_config(*prefixes)
    if provider == "cursor":
        return _cursor_config(*prefixes)
    raise RuntimeError(f"unsupported LLM provider: {provider!r}")


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM config for the chat/chain_generator path."""
    provider = normalise_provider(os.getenv("LLM_PROVIDER", "")) or _detect_provider()

    if provider in SUPPORTED_PROVIDERS:
        cfg = _config_for_provider(provider, "CEREBRUM_CHAT")
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
    """Factory Product Architect — cursor, leftover Kimi, or Claude.

    ``LLM_PROVIDER=cursor`` uses Cursor-family keys for HTTP draft
    (same host as Floor chat). Cursor BA / cli-pivot stays the generate path.

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
                f"Factory architect supports {' / '.join(SUPPORTED_PROVIDERS)} "
                f"(cursor is accepted); LLM_PROVIDER={raw} is not allowed for "
                "product architecture"
            ),
        }

    provider = explicit or _detect_provider() or "kimi"

    if provider == "cursor":
        cfg = _cursor_config("CEREBRUM_FACTORY")
        if cfg["mock"]:
            return cfg
        if not cfg["api_key"]:
            cfg["error"] = (
                "Factory architect was asked for Cursor but CURSOR_API_KEY "
                "(or CURSOR_AGENT_API_KEY / FACTORY_CURSOR_API_KEY) is not set; "
                "refusing to fall back to another provider — set the key or "
                "unset LLM_PROVIDER"
            )
        return cfg

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
