import os
import json
import httpx
import logging
from typing import List, Dict, Any

from .feature_mapper import fetch_block_registry

logger = logging.getLogger(__name__)

QWEN_API_KEY = os.getenv("QWEN_API_KEY") or os.getenv("CEREBRUM_LLM_API_KEY")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")


def _build_system_prompt(available_blocks: List[Dict[str, Any]], domain: str, docs_summary: str) -> str:
    block_list = "\n".join(
        f"- {b.get('name')}: {b.get('description', 'No description')}" for b in available_blocks
    )
    docs_section = f"\nUploaded documents summary:\n{docs_summary}\n" if docs_summary else ""
    return (
        "You are an AI solution architect for CerebrumDev.ai. "
        "Your job is to help users design an orchestrator chain of Cerebrum Blocks for their domain.\n\n"
        f"Domain: {domain}\n"
        f"Available blocks:\n{block_list}\n"
        f"{docs_section}\n"
        "When responding:\n"
        "1. Be concise and conversational.\n"
        "2. Ask clarifying questions if the request is vague.\n"
        "3. When you have enough information, propose a chain in the exact JSON format below.\n"
        "4. Also extract any business rules the user mentions (e.g., 'always flag urgent RFIs') as a list of strings.\n\n"
        "Chain JSON format:\n"
        '{"blocks": [{"id": "<block_name>", "params": {...}}], "connections": [{"from": 0, "to": 1}]}\n\n'
        "Return your response as JSON with three top-level keys:\n"
        '{"message": "<conversational reply to user>", "chain": <chain JSON or null>, "rules": ["rule 1", "rule 2"]}\n'
        "Only include 'chain' when you are ready to propose one."
    )


async def _call_llm(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Call the configured Qwen/DashScope LLM. Returns parsed JSON."""
    if not QWEN_API_KEY:
        raise RuntimeError("QWEN_API_KEY not configured")

    url = f"{QWEN_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": QWEN_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


def _mock_response(user_message: str, domain: str, available_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mock response for testing without an LLM key."""
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
) -> Dict[str, Any]:
    """Generate a chain suggestion and extract rules from a user message."""
    registry = await fetch_block_registry()
    available_blocks = list(registry.values())

    messages = [
        {"role": "system", "content": _build_system_prompt(available_blocks, domain, docs_summary)},
    ]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_message})

    try:
        result = await _call_llm(messages)
    except Exception as exc:
        if not QWEN_API_KEY:
            logger.warning("QWEN_API_KEY not set, using mock generator: %s", exc)
            result = _mock_response(user_message, domain, available_blocks)
        else:
            logger.exception("Qwen LLM call failed")
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
