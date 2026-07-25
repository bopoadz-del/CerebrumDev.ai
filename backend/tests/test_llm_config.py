"""Tests for unified LLM provider configuration."""

import os
import pytest
from unittest.mock import patch

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


def test_factory_llm_kimi_only_rejects_qwen():
    os.environ["LLM_PROVIDER"] = "qwen"
    cfg = get_factory_llm_config()
    assert cfg["provider"] == ""
    assert "Kimi-only" in cfg.get("error", "")


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

