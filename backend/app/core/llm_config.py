"""Unified LLM provider configuration for CerebrumDev.ai.

Supported providers:
- qwen      (DashScope / OpenAI-compatible)
- moonshot  (Moonshot AI / OpenAI-compatible)
- ollama    (Ollama /api/chat)

Set LLM_PROVIDER explicitly, or let the app auto-detect from env vars.
"""

import os
from typing import Dict, Any


def _detect_provider() -> str:
    """Auto-detect provider from configured credentials."""
    if os.getenv("QWEN_API_KEY"):
        return "qwen"
    if os.getenv("OLLAMA_URL"):
        return "ollama"
    if os.getenv("CEREBRUM_LLM_API_KEY"):
        return "moonshot"
    return ""


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM provider, model, base URL, and API key."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower() or _detect_provider()

    if provider == "qwen":
        return {
            "provider": "qwen",
            "api_key": os.getenv("QWEN_API_KEY", ""),
            "base_url": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "model": os.getenv("QWEN_MODEL", "qwen-plus"),
        }

    if provider == "moonshot":
        return {
            "provider": "moonshot",
            "api_key": os.getenv("CEREBRUM_LLM_API_KEY", ""),
            "base_url": os.getenv("CEREBRUM_LLM_BASE_URL", "https://api.moonshot.cn/v1"),
            "model": os.getenv("CEREBRUM_LLM_MODEL", "moonshot-v1-8k"),
        }

    if provider == "ollama":
        return {
            "provider": "ollama",
            "api_key": "",
            "base_url": os.getenv("OLLAMA_URL", ""),
            "model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
        }

    return {"provider": "", "api_key": "", "base_url": "", "model": ""}


def active_provider() -> str:
    return get_llm_config()["provider"]
