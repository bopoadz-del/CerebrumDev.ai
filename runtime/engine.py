"""Engine profiles — the brain is a config line; one function: complete(messages).

All three profiles speak OpenAI-compatible /chat/completions (Ollama local and
Ollama Cloud both expose it). One retry on any failure; second failure = STOP
(the gate-failed-twice law applied to the engine).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List

from . import stop


@dataclass(frozen=True)
class Engine:
    profile: str
    model: str
    complete: Callable[[List[Dict[str, str]]], str]


def _env(*names: str) -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return ""


def _chat(base_url: str, api_key: str, model: str, messages: List[Dict[str, str]]) -> str:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps({"model": model, "messages": messages, "temperature": 0}).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def _profile() -> Dict[str, str]:
    name = _env("ENGINE_PROFILE") or "cloud_api"
    if name == "cloud_api":
        return {
            "name": name,
            "base": _env("CEREBRUM_LLM_BASE_URL", "OPENAI_BASE_URL")
            or "https://api.openai.com/v1",
            "key": _env("CEREBRUM_LLM_API_KEY", "OPENAI_API_KEY"),
            "model": _env("CEREBRUM_LLM_MODEL", "OPENAI_MODEL") or "gpt-4o-mini",
        }
    if name == "ollama_cloud":
        return {
            "name": name,
            "base": (_env("OLLAMA_URL") or "https://ollama.com") + "/v1",
            "key": _env("OLLAMA_API_KEY"),
            "model": _env("OLLAMA_MODEL") or "kimi-k2.7-code:cloud",
        }
    if name == "local_sovereign":
        return {
            "name": name,
            "base": (_env("OLLAMA_URL") or "http://localhost:11434") + "/v1",
            "key": "",
            "model": _env("OLLAMA_MODEL") or "qwen3:32b",
        }
    stop.halt(
        "engine_profile_unknown",
        {"profile": name},
        "Set ENGINE_PROFILE to cloud_api | ollama_cloud | local_sovereign.",
    )
    return {}  # unreachable — halt raises


def resolve() -> Engine:
    p = _profile()
    if p["name"] != "local_sovereign" and not p["key"]:
        stop.halt(
            "engine_key_missing",
            {"profile": p["name"]},
            "Set the provider API key env var for this profile.",
        )

    def complete(messages: List[Dict[str, str]]) -> str:
        last_err = ""
        for _attempt in (1, 2):
            try:
                return _chat(p["base"], p["key"], p["model"], messages)
            except Exception as exc:  # noqa: BLE001 — any failure gets one retry
                last_err = str(exc)
        stop.halt(
            "engine_failed_twice",
            {"profile": p["name"], "error": last_err},
            "Restore engine connectivity, then resume the run.",
        )
        return ""  # unreachable

    return Engine(profile=p["name"], model=p["model"], complete=complete)
