import os
import json
import httpx
import logging
from typing import List, Dict, Any, Optional

from .feature_mapper import fetch_block_registry
from .block_taxonomy import BUILTIN_BLOCKS, OPTIONAL_BLOCKS
from .llm_config import get_llm_config, active_provider
from .source_pack_loader import get_source_pack

logger = logging.getLogger(__name__)


def _build_source_pack_context(domain: str) -> str:
    """Return a source-pack guidance section for the system prompt.

    Returns an empty string when the domain has no source pack or when the
    shelf cannot be loaded, so chain generation never breaks because of
    metadata issues.
    """
    try:
        pack = get_source_pack(domain)
    except Exception:
        logger.exception("Failed to load source pack for domain=%s", domain)
        return ""

    if not pack:
        return ""

    blocks = ", ".join(pack.get("blocks", []))

    return (
        f"\nDomain guidance for {domain}:\n"
        f"Expert role: {pack.get('expert_prompt', '')}\n"
        f"Workflow: {pack.get('workflow', '')}\n"
        f"Recommended blocks: {blocks}\n"
        "Prefer the recommended source-pack blocks when proposing a chain for this domain. "
        "The domain v2 block is the primary domain-specific analysis block. "
        "For domain-specific analysis, prefer the domain v2 block over generic built-in blocks "
        "such as llm_enhancer, knowledge, memory, vector_search, or validation_pipeline. "
        "Use generic built-ins only to support ingestion, retrieval, validation, memory, or UI behavior. "
        "Do not replace the domain v2 block with generic built-ins when the user's request is clearly domain-specific. "
        "Include chat when the user needs a conversational interface or Q&A. "
        "Use recommended blocks only if they are present in the available block registry. "
        "Never invent block IDs.\n"
    )


def _build_system_prompt(
    available_blocks: List[Dict[str, Any]],
    domain: str,
    docs_summary: str,
    session_state: str = "",
) -> str:
    optional_available = [b for b in available_blocks if b.get("name") in OPTIONAL_BLOCKS]
    optional_list = "\n".join(
        f"- {b.get('name')}: {b.get('description', 'No description')}" for b in optional_available
    ) or "- (none)"
    docs_section = f"\nUploaded documents summary:\n{docs_summary}\n" if docs_summary else ""
    state_section = (
        "\nSession state (authoritative — trust this over your own inference):\n"
        f"{session_state}\n"
        if session_state
        else ""
    )
    source_pack_section = _build_source_pack_context(domain)
    return (
        "You are an AI solution architect for CerebrumDev.ai. "
        "Your job is to help users configure and optionally extend their sovereign AI instance.\n\n"
        f"Domain: {domain}\n"
        "The platform already includes these built-in blocks automatically: "
        f"{', '.join(BUILTIN_BLOCKS)}.\n"
        "You do NOT need to propose these in the chain; they are always available.\n\n"
        "Optional Fork primitives the user can add on top:\n"
        f"{optional_list}\n"
        f"{source_pack_section}"
        f"{state_section}"
        f"{docs_section}\n"
        "When responding:\n"
        "1. Be concise and conversational.\n"
        "2. Ask clarifying questions if the request is vague.\n"
        "3. If the user asks what blocks are available, tell them the domain kit + built-ins are already included, "
        "and list the optional primitives they can add.\n"
        "4. Only propose optional blocks in the chain when the user explicitly asks for that capability.\n"
        "5. When you have enough information, propose a chain in the exact JSON format below.\n"
        "6. Also extract any business rules the user mentions (e.g., 'always flag urgent RFIs') as a list of strings.\n\n"
        "Chain JSON format:\n"
        '{"blocks": [{"id": "<block_name>", "params": {...}}], "connections": [{"from": 0, "to": 1}]}\n\n'
        "Return your response as JSON with three top-level keys:\n"
        '{"message": "<conversational reply to user>", "chain": <chain JSON or null>, "rules": ["rule 1", "rule 2"]}\n'
        "Only include 'chain' when you are ready to propose one.\n"
        "7. Never claim that you or the platform have built, generated, deployed, exported, "
        "or modified anything. Platform actions are reported only by explicit system events "
        "in the chat (blueprint / generation cards).\n"
        "8. Answer status questions strictly from the Session state section above. If something "
        "is not stated there, say you do not have that information and point the user to the "
        "Factory Floor flow.\n"
        "9. Never invent URLs, prices, credentials, dates, product names, or deployment targets."
    )


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


async def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float | None = None,
) -> Dict[str, Any]:
    """Call an OpenAI-compatible chat completion endpoint (Qwen, Moonshot, etc.)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    # Omit temperature unless explicitly configured: reasoning models
    # (kimi-k2.x) reject any explicit value other than 1.
    if temperature is not None:
        payload["temperature"] = temperature
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


async def _call_ollama(
    base_url: str, model: str, messages: List[Dict[str, str]], api_key: str = ""
) -> Dict[str, Any]:
    """Call a local/remote Ollama server. Returns parsed JSON."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.3},
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Ollama returned empty content")
        return _extract_json(content)


async def _call_llm(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Call the configured LLM. Returns parsed JSON."""
    cfg = get_llm_config()
    if cfg.get("mock"):
        raise RuntimeError("LLM mock mode — no network call")
    provider = cfg["provider"]
    if provider in ("qwen", "moonshot", "kimi"):
        return await _call_openai_compatible(
            cfg["base_url"], cfg["api_key"], cfg["model"], messages, cfg.get("temperature")
        )
    if provider == "ollama":
        return await _call_ollama(cfg["base_url"], cfg["model"], messages, cfg["api_key"])
    raise RuntimeError("No LLM provider configured")


def _mock_response(user_message: str, domain: str, available_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mock response for testing without an LLM."""
    block_names = [b.get("name") for b in available_blocks]
    chain_blocks = []
    connections = []
    if "pdf" in block_names:
        chain_blocks.append({"id": "pdf", "params": {"extract_tables": True}})
    if "ocr" in block_names and ("image" in user_message.lower() or "scan" in user_message.lower()):
        chain_blocks.append({"id": "ocr", "params": {"preprocess": True}})
    if "chat" in block_names:
        chain_blocks.append({"id": "chat", "params": {"temperature": 0.7}})

    if len(chain_blocks) > 1:
        connections = [{"from": i, "to": i + 1} for i in range(len(chain_blocks) - 1)]

    rules = []
    if "rule" in user_message.lower() or "always" in user_message.lower():
        rules.append("Flag urgent items explicitly mentioned by the user")

    return {
        "message": (
            f"I've drafted a starter chain for your {domain} workflow. "
            "You can refine it by adding more details or rules."
        ),
        "chain": {"blocks": chain_blocks, "connections": connections} if chain_blocks else None,
        "rules": rules,
    }


async def generate_chain_suggestion(
    domain: str,
    user_message: str,
    chat_history: List[Dict[str, str]],
    docs_summary: str,
    session_state: str = "",
) -> Dict[str, Any]:
    """Generate a chain suggestion and extract rules from a user message.

    session_state: authoritative session facts (blueprint / generation
    status) used to ground the conversational reply — the model must never
    invent platform actions or status.
    """
    registry = await fetch_block_registry()
    available_blocks = list(registry.values())

    messages = [
        {
            "role": "system",
            "content": _build_system_prompt(available_blocks, domain, docs_summary, session_state),
        },
    ]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_message})

    cfg = get_llm_config()
    try:
        if cfg.get("mock") or not cfg.get("provider"):
            result = _mock_response(user_message, domain, available_blocks)
        else:
            result = await _call_llm(messages)
    except Exception as exc:
        if not active_provider() or cfg.get("mock"):
            logger.warning("No live LLM provider, using mock generator: %s", exc)
            result = _mock_response(user_message, domain, available_blocks)
        else:
            logger.exception("LLM call failed")
            raise

    return {
        "message": result.get("message", ""),
        "chain": result.get("chain") or None,
        "rules": result.get("rules") or [],
    }


def validate_chain(chain: Dict[str, Any], available_block_names: List[str]) -> bool:
    """Basic validation: all block IDs exist."""
    if not isinstance(chain, dict):
        return False
    blocks = chain.get("blocks", [])
    if not blocks:
        return False
    for block in blocks:
        bid = block.get("id")
        if not bid or bid not in available_block_names:
            return False
    connections = chain.get("connections", [])
    for conn in connections:
        if not isinstance(conn.get("from"), int) or not isinstance(conn.get("to"), int):
            return False
    return True


def check_chain_quality(
    domain: str, chain: Optional[Dict[str, Any]], validation_passed: bool
) -> Optional[Dict[str, Any]]:
    """Soft post-validation quality check returning metadata only.

    This function never rejects a chain or mutates it. It inspects the
    domain source pack and reports whether the primary domain v2 block is
    present in the proposed chain.
    """
    if not validation_passed:
        return None
    if not chain or not isinstance(chain, dict):
        return None

    try:
        pack = get_source_pack(domain)
    except Exception:
        logger.exception("Failed to load source pack for domain=%s", domain)
        return None

    if not pack:
        return None

    domain_v2_block = None
    for block_id in pack.get("blocks", []):
        # formula_executor_v2 is a shared reasoning support block, not the
        # primary domain-specific v2 block we want to enforce here.
        if isinstance(block_id, str) and block_id.endswith("_v2") and block_id != "formula_executor_v2":
            domain_v2_block = block_id
            break

    if not domain_v2_block:
        return None

    proposed_ids = {block.get("id") for block in chain.get("blocks", [])}
    if domain_v2_block in proposed_ids:
        return {"status": "ok", "warnings": []}

    return {
        "status": "needs_review",
        "warnings": [
            {
                "code": "missing_domain_v2_block",
                "message": (
                    f"This {domain} chain does not include {domain_v2_block}, "
                    f"the primary {domain} analysis block."
                ),
                "suggested_block": domain_v2_block,
            }
        ],
    }
