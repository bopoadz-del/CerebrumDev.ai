import os
import json
import logging
from typing import Any, List, Dict
import httpx

logger = logging.getLogger(__name__)

QWEN_API_KEY = os.getenv("QWEN_API_KEY") or os.getenv("CEREBRUM_LLM_API_KEY")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")


def parse_rules(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Convert free-text rules into structured rule objects.

    Uses Qwen/DashScope when QWEN_API_KEY is configured; otherwise falls back
    to the deterministic parser.
    """
    if not rule_texts:
        return []

    if QWEN_API_KEY:
        try:
            return _parse_with_llm(rule_texts)
        except Exception as exc:
            logger.warning("LLM rule parsing failed, falling back to deterministic parser: %s", exc)

    return _parse_naive(rule_texts)


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


def _parse_with_llm(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Ask Qwen to extract trigger/action/snippet for each rule."""
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

    result = _call_qwen(messages)

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
