"""LLM provider interface + Azure OpenAI adapter, OpenAI-compatible fallback,
and a deterministic fake provider.

No credentials are hardcoded and none are required for tests or the local
pilot (the fake provider is fully deterministic and grounds strictly on the
supplied context). Provider failures raise ``ProviderError`` — they never
cause fabricated action output.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class ProviderError(RuntimeError):
    """Raised on provider misconfiguration or upstream failure."""


class LLMProvider:
    name: str = "base"

    def available(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    async def complete(
        self, system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 800
    ) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class AzureOpenAIProvider(LLMProvider):
    name = "azure_openai"

    def __init__(self) -> None:
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
        self.deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        self.embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")

    def available(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment)

    async def complete(
        self, system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 800
    ) -> str:
        if not self.available():
            raise ProviderError("Azure OpenAI is not fully configured")
        import httpx

        url = (
            f"{self.endpoint}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url, json=payload, headers={"api-key": self.api_key}
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Azure OpenAI request failed: {exc}") from exc


class OpenAICompatibleProvider(LLMProvider):
    """Fallback for any OpenAI-compatible endpoint (OpenAI, Qwen, Ollama-openai)."""

    name = "openai_compatible"

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", os.getenv("RETAILOPS_LLM_API_KEY", ""))
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", os.getenv("RETAILOPS_LLM_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.model = os.getenv("RETAILOPS_LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(
        self, system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 800
    ) -> str:
        if not self.available():
            raise ProviderError("OpenAI-compatible provider is not configured")
        import httpx

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"OpenAI-compatible request failed: {exc}") from exc


class FakeProvider(LLMProvider):
    """Deterministic provider that grounds strictly on supplied context.

    It never invents facts: the answer is assembled from the context excerpts
    passed in the prompt. Used for tests and the credential-free local pilot.
    """

    name = "fake"

    def available(self) -> bool:
        return True

    async def complete(
        self, system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 800
    ) -> str:
        # The orchestrator embeds context as lines prefixed with "[n] ".
        context_lines = [
            line.strip()
            for line in prompt.splitlines()
            if line.strip().startswith("[")
        ]
        question = ""
        for line in prompt.splitlines():
            if line.lower().startswith("question:"):
                question = line.split(":", 1)[1].strip()
        if not context_lines:
            return (
                "I could not find supporting information in the project's "
                "documents to answer this question."
            )
        head = context_lines[:3]
        joined = " ".join(head)
        prefix = f"Based on the project documents, regarding '{question}': " if question else "Based on the project documents: "
        return prefix + joined


def get_provider(preference: Optional[str] = None) -> LLMProvider:
    """Resolve a provider. Default is deterministic + credential-free."""
    preference = preference or os.getenv("RETAILOPS_LLM_PROVIDER", "auto")
    if preference == "fake":
        return FakeProvider()
    if preference == "azure":
        provider = AzureOpenAIProvider()
        if not provider.available():
            raise ProviderError("Azure OpenAI selected but not configured")
        return provider
    if preference == "openai":
        provider = OpenAICompatibleProvider()
        if not provider.available():
            raise ProviderError("OpenAI-compatible selected but not configured")
        return provider
    # auto: prefer Azure, then OpenAI-compatible, else deterministic fake.
    azure = AzureOpenAIProvider()
    if azure.available():
        return azure
    openai = OpenAICompatibleProvider()
    if openai.available():
        return openai
    return FakeProvider()
