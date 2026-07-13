"""LLM provider tests: Azure config detection + deterministic fake provider."""

from __future__ import annotations

import pytest

from app.retailops.providers import (
    AzureOpenAIProvider,
    FakeProvider,
    ProviderError,
    get_provider,
)


def test_azure_provider_config_detection(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    assert AzureOpenAIProvider().available() is False

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt4o")
    p = AzureOpenAIProvider()
    assert p.available() is True
    assert p.api_version  # has a default version


def test_get_provider_defaults_to_fake_without_credentials(monkeypatch):
    for var in [
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
        "OPENAI_API_KEY", "RETAILOPS_LLM_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RETAILOPS_LLM_PROVIDER", "auto")
    assert isinstance(get_provider(), FakeProvider)


def test_azure_selected_but_unconfigured_raises(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(ProviderError):
        get_provider("azure")


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic_and_grounded():
    p = FakeProvider()
    prompt = "Question: what is the policy?\n\nContext:\n[1] Report within 15 minutes. (sop.txt)"
    a = await p.complete("sys", prompt)
    b = await p.complete("sys", prompt)
    assert a == b
    assert "15 minutes" in a  # grounded strictly on provided context


@pytest.mark.asyncio
async def test_fake_provider_no_context_is_honest():
    p = FakeProvider()
    a = await p.complete("sys", "Question: anything?\n\nContext:")
    assert "could not find" in a.lower()
