"""Floor suggestions must use the primary LLM, not a dead OpenRouter key.

Live failure: generate_chain_suggestion posted to
https://openrouter.ai/api/v1/chat/completions, 401'd, and Floor chat hard-failed
while /ready reported llm_configured (CEREBRUM_LLM_API_KEY / Kimi).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.chain_generator import (
    _SOFT_FAIL_MESSAGE,
    _call_llm,
    generate_chain_suggestion,
)
from app.core.llm_config import get_llm_config


def _ok_json(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": __import__("json").dumps(payload)}}]
    }
    return resp


def _http_error(status: int, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"{status} Unauthorized" if status == 401 else f"{status}",
        request=request,
        response=response,
    )


@pytest.fixture
def primary_and_broken_openrouter(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", "sk-moonshot-shared")
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CEREBRUM_LLM_MODEL", "minimax/minimax-m3:free")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_MODEL", "moonshot-v1-8k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dead")
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_MODEL", "minimax/minimax-m3:free")
    monkeypatch.delenv("KIMI_MOCK", raising=False)
    monkeypatch.delenv("CEREBRUM_LLM_MOCK", raising=False)


def test_config_prefers_moonshot_over_openrouter_base(primary_and_broken_openrouter):
    cfg = get_llm_config()
    assert cfg["api_key"] == "sk-moonshot-shared"
    assert cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert cfg["model"] == "moonshot-v1-8k"
    assert "openrouter" not in cfg["base_url"]


@pytest.mark.anyio
async def test_call_llm_hits_moonshot_first(primary_and_broken_openrouter):
    posts: list[str] = []

    async def fake_post(self, url, *, json=None, headers=None):
        posts.append(url)
        if "openrouter" in url:
            raise _http_error(401, url)
        return _ok_json({"message": "from-kimi", "chain": None, "rules": []})

    with patch.object(httpx.AsyncClient, "post", fake_post):
        result = await _call_llm([{"role": "user", "content": "hi"}])

    assert result["message"] == "from-kimi"
    assert posts, "primary Moonshot host must be called"
    assert "openrouter.ai" not in posts[0]
    assert "api.moonshot.ai" in posts[0]


@pytest.mark.anyio
async def test_call_llm_falls_back_to_openrouter_after_primary_fails(
    primary_and_broken_openrouter, monkeypatch
):
    # Use a paid-denied free slug so the fallback leg arms.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fallback-ok")
    posts: list[str] = []

    async def fake_post(self, url, *, json=None, headers=None):
        posts.append(url)
        if "openrouter" in url:
            return _ok_json({"message": "from-openrouter", "chain": None, "rules": []})
        raise _http_error(429, url)

    with patch.object(httpx.AsyncClient, "post", fake_post):
        result = await _call_llm([{"role": "user", "content": "hi"}])

    assert result["message"] == "from-openrouter"
    assert any("api.moonshot.ai" in u for u in posts)
    assert any("openrouter.ai" in u for u in posts)


@pytest.mark.anyio
async def test_openrouter_401_does_not_hard_fail_when_primary_configured(
    primary_and_broken_openrouter,
):
    async def fake_post(self, url, *, json=None, headers=None):
        raise _http_error(401, url)

    with patch.object(httpx.AsyncClient, "post", fake_post), patch(
        "app.core.chain_generator.fetch_block_registry",
        new=AsyncMock(return_value={}),
    ):
        result = await generate_chain_suggestion(
            domain="construction",
            user_message="what can you do?",
            chat_history=[],
            docs_summary="",
            session_state="No platform blueprint has been drafted.",
        )

    assert result["message"] == _SOFT_FAIL_MESSAGE
    assert result["chain"] is None
    assert result["rules"] == []


@pytest.mark.anyio
async def test_openrouter_only_401_is_soft_failure(monkeypatch):
    """No Moonshot key: OpenRouter is primary; 401 still must not raise."""
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dead")
    monkeypatch.delenv("CEREBRUM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_CHAT_LLM_API_KEY", raising=False)

    async def fake_post(self, url, *, json=None, headers=None):
        raise _http_error(401, url)

    with patch.object(httpx.AsyncClient, "post", fake_post), patch(
        "app.core.chain_generator.fetch_block_registry",
        new=AsyncMock(return_value={}),
    ):
        result = await generate_chain_suggestion(
            domain="construction",
            user_message="hello",
            chat_history=[],
            docs_summary="",
        )

    assert result["message"] == _SOFT_FAIL_MESSAGE
    assert result["chain"] is None
