import os
import json
import logging
from typing import Any, List, Dict
import httpx

logger = logging.getLogger(__name__)

QWEN_API_KEY = os.getenv("QWEN_API_KEY") or os.getenv("CEREBRUM_LLM_API_KEY")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")

OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:480b-cloud")


def parse_rules(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Convert free-text rules into structured rule objects.

    Uses the configured LLM (Qwen or Ollama) when available; otherwise falls back
    to the deterministic parser.
    """
    if not rule_texts:
        return []

    provider = _active_provider()
    if provider:
        try:
            return _parse_with_llm(provider, rule_texts)
        except Exception as exc:
            logger.warning("LLM rule parsing failed, falling back to deterministic parser: %s", exc)

    return _parse_naive(rule_texts)


def _active_provider() -> str:
    if QWEN_API_KEY:
        return "qwen"
    if OLLAMA_URL:
        return "ollama"
    return ""


def _parse_naive(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Deterministic rule parser."""
    parsed = []
    for text in rule_texts:
        text = text.strip()
        if not text:
            continue
        parsed.append({
            "raw": text,
            "trigger": _extract_trigger(text),
            "action": _extract_action(text),
            "code_snippet": _generate_snippet(text),
        })
    return parsed


def _call_qwen(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Call Qwen/DashScope with a JSON-object request (synchronous)."""
    url = f"{QWEN_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": QWEN_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


def _extract_json(text: str) -> Dict[str, Any]:
    """Strip Markdown fences and parse the first JSON object in *text*."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    text = text.strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output")
    depth = 0
    end = start
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if depth != 0:
        raise ValueError("Unbalanced JSON object in model output")
    return json.loads(text[start:end])


def _call_ollama(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Call Ollama /api/chat (synchronous)."""
    url = f"{OLLAMA_URL.rstrip('/')}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Ollama returned empty content")
        return _extract_json(content)


def _parse_with_llm(provider: str, rule_texts: List[str]) -> List[Dict[str, str]]:
    """Ask the configured LLM to extract trigger/action/snippet for each rule."""
    system_prompt = (
        "You are a business-rule parser. Given a list of free-text rules, "
        "return a JSON object with a single key 'rules' containing a list of objects. "
        "Each object must have the keys: raw (string), trigger (string), action (string), "
        "code_snippet (string, a short Python-like snippet)."
    )
    user_prompt = json.dumps({"rules": rule_texts}, ensure_ascii=False)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if provider == "qwen":
        result = _call_qwen(messages)
    else:
        result = _call_ollama(messages)

    parsed = []
    for item in result.get("rules", []):
        raw = item.get("raw", "")
        if not raw:
            continue
        parsed.append({
            "raw": raw,
            "trigger": item.get("trigger") or _extract_trigger(raw),
            "action": item.get("action") or _extract_action(raw),
            "code_snippet": item.get("code_snippet") or _generate_snippet(raw),
        })
    return parsed or _parse_naive(rule_texts)


def _extract_trigger(text: str) -> str:
    """Naive trigger extraction."""
    lower = text.lower()
    if "if " in lower:
        return text.split("if ", 1)[1].split(" then", 1)[0].strip(" .")
    return "always"


def _extract_action(text: str) -> str:
    """Naive action extraction."""
    lower = text.lower()
    if " then " in lower:
        return text.split(" then ", 1)[1].strip(" .")
    if "always " in lower:
        return text.lower().replace("always ", "").strip(" .")
    return text


def _generate_snippet(text: str) -> str:
    """Generate a placeholder Python snippet for the rule."""
    trigger = _extract_trigger(text)
    action = _extract_action(text)
    return (
        f"# Rule: {text}\n"
        f"if context.matches({trigger!r}):\n"
        f"    context.apply_action({action!r})\n"
    )
