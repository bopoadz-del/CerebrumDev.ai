"""Tests for unified LLM provider configuration."""

import os
import pytest
from unittest.mock import patch

from app.core.llm_config import get_llm_config, active_provider


@pytest.fixture(autouse=True)
def _clear_env():
    keys = [
        "LLM_PROVIDER",
        "QWEN_API_KEY",
        "QWEN_BASE_URL",
        "QWEN_MODEL",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_LLM_BASE_URL",
        "CEREBRUM_LLM_MODEL",
        "OLLAMA_URL",
        "OLLAMA_MODEL",
        "OLLAMA_API_KEY",
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


def test_explicit_ollama():
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_URL"] = "http://localhost:11434"
    os.environ["OLLAMA_MODEL"] = "glm-5.2:cloud"
    cfg = get_llm_config()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "glm-5.2:cloud"
    assert cfg["base_url"] == "http://localhost:11434"
    assert cfg["api_key"] == ""


def test_explicit_ollama_cloud_with_api_key():
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_URL"] = "https://ollama.com"
    os.environ["OLLAMA_MODEL"] = "kimi-k2.7-code:cloud"
    os.environ["OLLAMA_API_KEY"] = "sk-ollama-cloud"
    cfg = get_llm_config()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "kimi-k2.7-code:cloud"
    assert cfg["base_url"] == "https://ollama.com"
    assert cfg["api_key"] == "sk-ollama-cloud"


def test_explicit_qwen():
    os.environ["LLM_PROVIDER"] = "qwen"
    os.environ["QWEN_API_KEY"] = "sk-qwen"
    os.environ["QWEN_MODEL"] = "qwen-max"
    cfg = get_llm_config()
    assert cfg["provider"] == "qwen"
    assert cfg["api_key"] == "sk-qwen"
    assert cfg["model"] == "qwen-max"


def test_explicit_moonshot():
    os.environ["LLM_PROVIDER"] = "moonshot"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moon"
    os.environ["CEREBRUM_LLM_MODEL"] = "moonshot-v1-32k"
    cfg = get_llm_config()
    assert cfg["provider"] == "moonshot"
    assert cfg["api_key"] == "sk-moon"
    assert cfg["model"] == "moonshot-v1-32k"


def test_auto_detect_qwen():
    os.environ["QWEN_API_KEY"] = "sk-qwen"
    cfg = get_llm_config()
    assert cfg["provider"] == "qwen"


def test_auto_detect_ollama():
    os.environ["OLLAMA_URL"] = "http://ollama:11434"
    cfg = get_llm_config()
    assert cfg["provider"] == "ollama"


def test_no_provider():
    cfg = get_llm_config()
    assert cfg["provider"] == ""
