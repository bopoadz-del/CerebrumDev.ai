"""Tests for unified LLM provider configuration."""

import os
import pytest

from app.core.llm_config import get_factory_llm_config, get_llm_config, active_provider


@pytest.fixture(autouse=True)
def _clear_env():
    keys = [
        "LLM_PROVIDER",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_LLM_BASE_URL",
        "CEREBRUM_LLM_MODEL",
        "KIMI_API_KEY",
        "KIMI_BASE_URL",
        "KIMI_MODEL",
        "KIMI_MOCK",
        "CEREBRUM_LLM_MOCK",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_BASE_URL",
        "CEREBRUM_CHAT_LLM_MODEL",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_BASE_URL",
        "CEREBRUM_FACTORY_LLM_MODEL",
        "OPENROUTER_API_KEY",
        "FACTORY_LLM_FALLBACK_API_KEY",
    ]
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_explicit_moonshot_aliases_to_kimi():
    os.environ["LLM_PROVIDER"] = "moonshot"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moon"
    os.environ["CEREBRUM_LLM_MODEL"] = "moonshot-v1-32k"
    cfg = get_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["api_key"] == "sk-moon"
    assert cfg["model"] == "moonshot-v1-32k"


def test_explicit_kimi():
    os.environ["LLM_PROVIDER"] = "kimi"
    os.environ["KIMI_API_KEY"] = "sk-kimi"
    os.environ["KIMI_MODEL"] = "kimi-k2"
    cfg = get_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["api_key"] == "sk-kimi"
    assert cfg["model"] == "kimi-k2"


def test_factory_llm_rejects_an_unsupported_provider():
    """Behaviour unchanged; only the message names the supported set.

    Was `test_factory_llm_kimi_only_rejects_qwen`, asserting the error said
    "Kimi-only". Claude support makes that string false, so the assertion moved
    to what actually matters: an unknown provider is still refused, still with
    an empty provider and an error, and the message says what IS allowed.
    """
    os.environ["LLM_PROVIDER"] = "qwen"
    cfg = get_factory_llm_config()
    assert cfg["provider"] == ""
    error = cfg.get("error", "")
    assert "qwen" in error
    assert "kimi" in error and "claude" in error


def test_factory_llm_mock():
    os.environ["KIMI_MOCK"] = "1"
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["mock"] is True


def test_kimi_mock_does_not_activate_live_kit_provider():
    """Kit chat must stay offline when only mock flags are set (no API key)."""
    os.environ["KIMI_MOCK"] = "1"
    cfg = get_llm_config()
    assert cfg["provider"] == ""
    assert cfg["mock"] is True
    assert active_provider() == ""


def test_no_provider():
    cfg = get_llm_config()
    assert cfg["provider"] == ""


def test_chat_and_factory_use_separate_models():
    """Chat and Factory architect can point at different Kimi models."""
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-shared"
    os.environ["CEREBRUM_CHAT_LLM_MODEL"] = "kimi-k2.7"
    os.environ["CEREBRUM_FACTORY_LLM_MODEL"] = "kimi-k2.7-code"
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg["model"] == "kimi-k2.7"
    assert factory_cfg["model"] == "kimi-k2.7-code"
    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"


def test_chat_and_factory_fallback_to_shared_vars():
    """When scoped vars are absent, both paths fall back to CEREBRUM_LLM_*."""
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-shared"
    os.environ["CEREBRUM_LLM_MODEL"] = "moonshot-v1-8k"
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg["model"] == "moonshot-v1-8k"
    assert factory_cfg["model"] == "moonshot-v1-8k"


def test_openrouter_base_uses_openrouter_key_not_moonshot_keys():
    """Pointing CEREBRUM_LLM_BASE_URL at OpenRouter must not send a Moonshot key."""
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"
    assert chat_cfg["base_url"] == "https://openrouter.ai/api/v1"
    assert factory_cfg["base_url"] == "https://openrouter.ai/api/v1"
    assert chat_cfg["api_key"] == "sk-or-test"
    assert factory_cfg["api_key"] == "sk-or-test"
    assert "error" not in factory_cfg


def test_openrouter_base_is_case_insensitive_and_prefers_path_key():
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://OpenRouter.AI/api/v1"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-or-chat-override"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"

    chat_cfg = get_llm_config()
    assert chat_cfg["api_key"] == "sk-or-chat-override"
    assert chat_cfg["base_url"] == "https://OpenRouter.AI/api/v1"


def test_moonshot_base_still_uses_kimi_keys_when_openrouter_key_present():
    """Default Moonshot host must keep using Moonshot credentials."""
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"
    assert chat_cfg["api_key"] == "sk-moonshot-kimi"
    assert factory_cfg["api_key"] == "sk-moonshot-kimi"
    assert chat_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert factory_cfg["base_url"] == "https://api.moonshot.ai/v1"


def test_openrouter_base_without_openrouter_key_does_not_use_moonshot_key():
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["api_key"] == ""
    assert factory_cfg["api_key"] == ""
    assert "OPENROUTER_API_KEY" in factory_cfg.get("error", "")


def test_openrouter_key_alone_activates_chat_when_base_is_openrouter():
    """Floor chat must not require a Moonshot key just to talk to OpenRouter."""
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"
    assert chat_cfg["api_key"] == "sk-or-test"
    assert factory_cfg["api_key"] == "sk-or-test"


def test_openrouter_base_uses_factory_fallback_api_key_when_openrouter_unset():
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["FACTORY_LLM_FALLBACK_API_KEY"] = "sk-or-fallback"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["api_key"] == "sk-or-fallback"
    assert factory_cfg["api_key"] == "sk-or-fallback"

