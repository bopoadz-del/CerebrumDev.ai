"""Tests for Ollama local and cloud calling in chain_generator."""

from __future__ import annotations

import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.core.chain_generator import _call_ollama, _call_llm
from app.core.llm_config import get_llm_config


def _mock_ollama_response(content: dict) -> AsyncMock:
    """Return an async mock for httpx.AsyncClient.post that yields a response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "message": {"content": json.dumps(content)}
    }
    mock_resp.raise_for_status = lambda: None
    return AsyncMock(return_value=mock_resp)


@pytest.mark.anyio
async def test_call_ollama_local_no_auth_header():
    """Local Ollama without API key sends no Authorization header."""
    mock_post = _mock_ollama_response({"message": "hi", "chain": None, "rules": []})

    with patch("httpx.AsyncClient.post", mock_post):
        await _call_ollama("http://localhost:11434", "qwen3:32b", [])

    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert "headers" in call_kwargs
    assert "Authorization" not in call_kwargs["headers"]
    assert call_kwargs["json"]["model"] == "qwen3:32b"


@pytest.mark.anyio
async def test_call_ollama_cloud_sends_bearer_auth():
    """Ollama Cloud with API key sends Authorization Bearer header."""
    mock_post = _mock_ollama_response({"message": "hi", "chain": None, "rules": []})

    with patch("httpx.AsyncClient.post", mock_post):
        await _call_ollama(
            "https://ollama.com", "kimi-k2.7-code:cloud", [], api_key="sk-ollama-cloud"
        )

    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-ollama-cloud"
    assert call_kwargs["json"]["model"] == "kimi-k2.7-code:cloud"


@pytest.mark.anyio
async def test_call_ollama_url_appends_api_chat():
    """URL remains {OLLAMA_URL}/api/chat."""
    mock_post = _mock_ollama_response({"message": "hi", "chain": None, "rules": []})

    with patch("httpx.AsyncClient.post", mock_post):
        await _call_ollama("https://ollama.com", "kimi-k2.7-code:cloud", [])

    mock_post.assert_called_once()
    call_args, _ = mock_post.call_args
    assert call_args[0] == "https://ollama.com/api/chat"


@pytest.mark.anyio
async def test_call_llm_passes_api_key_to_ollama(monkeypatch):
    """_call_llm forwards the Ollama API key from config."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "https://ollama.com")
    monkeypatch.setenv("OLLAMA_MODEL", "kimi-k2.7-code:cloud")
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-ollama-cloud")

    mock_post = _mock_ollama_response({"message": "hi", "chain": None, "rules": []})

    with patch("httpx.AsyncClient.post", mock_post):
        await _call_llm([])

    cfg = get_llm_config()
    assert cfg["provider"] == "ollama"
    assert cfg["api_key"] == "sk-ollama-cloud"
    call_args, call_kwargs = mock_post.call_args
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-ollama-cloud"
