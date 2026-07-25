"""Product Architect — drafts product_blueprint.v1 inside the Factory.

Today Steward may be predefined (golden YAML). The architect can:
- load a checked-in blueprint (deterministic / mock / golden path)
- draft from a brief using the LLM when ARCHITECT_LLM_DRAFTING_ENABLED is on
- fall back to deterministic keyword drafting (always available, no keys)

Architecture is always a validated ProductBlueprint; generation stays fail-closed
via the capability planner + dual registry. LLM drafting is fail-SAFE: any LLM
error, malformed payload, or all-foreign block ids falls back to the keyword
drafter — a draft is always produced, and block ids are always filtered
against the dual registry (never invent blocks).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml

from app.core.llm_config import get_factory_llm_config
from app.factory.blueprint import FactoryScenario, ProductBlueprint, load_blueprint
from app.factory.dual_registry import DualRegistryError, dual_registered_ids
from app.factory.generator import ProductGenerator, git_head
from app.factory.paths import factory_repo_root
from app.factory.planner import CapabilityPlanner, ProductPlan

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return factory_repo_root()


def steward_golden_path() -> Path:
    return _repo_root() / "blueprints" / "steward" / "steward.v1.yaml"


# --- LLM drafting (gated, fail-safe) -----------------------------------------

LLM_DRAFTING_ENV = "ARCHITECT_LLM_DRAFTING_ENABLED"


def llm_drafting_enabled() -> bool:
    """Env gate for LLM-powered brief drafting. Default OFF (house pattern)."""
    return os.getenv(LLM_DRAFTING_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_LLM_DRAFT_SYSTEM = """You are the Cerebrum product architect. Given a user \
brief for a software platform, draft a product blueprint as JSON.

Return ONLY a JSON object with this shape:
{
  "product_name": "<human-readable product name>",
  "vertical": "<one or two word vertical slug, e.g. fleet_management>",
  "summary": "<one paragraph: what the product does and who it serves>",
  "capabilities": [
    {
      "id": "<snake_case capability id>",
      "description": "<what this capability does>",
      "block_ids": ["<ids from the AVAILABLE BLOCKS list only>"],
      "strategy_hint": "REUSE"
    }
  ]
}

Rules:
- 3 to 8 capabilities, ordered by importance.
- block_ids may ONLY contain ids from the AVAILABLE BLOCKS list. Never invent ids.
- If no available block fits a capability, use "block_ids": [] and
  "strategy_hint": "GENERATE".
- vertical must be lowercase snake_case.
"""


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


def _llm_json_call(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Synchronous JSON call against the configured LLM provider.

    Mirrors core.chain_generator's async callers but stays sync so the
    architect's call sites (routers, chat flow, pipeline) are untouched.
    Raises on any failure — the caller falls back to keyword drafting.
    """
    cfg = get_factory_llm_config()
    if cfg.get("mock"):
        raise RuntimeError("LLM mock mode — no network call")
    provider = cfg.get("provider")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    if provider in ("moonshot", "kimi"):
        url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        # Omit temperature unless explicitly configured: reasoning models
        # (kimi-k2.x) reject any explicit value other than 1.
        if cfg.get("temperature") is not None:
            payload["temperature"] = cfg["temperature"]
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)

    raise RuntimeError("No LLM provider configured")


def _slug(text: str, default: str = "product") -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower())[:48].strip("-")
    return slug or default


def _snake_slug(text: str, default: str = "product") -> str:
    """Normalize a brief or LLM vertical into lowercase snake_case."""
    normalized = (text or "").lower().replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")[:48]
    return slug or default


def _blueprint_from_llm_payload(
    data: Dict[str, Any],
    brief: str,
    vertical_hint: Optional[str],
    dual_ids: List[str],
) -> ProductBlueprint:
    """Validate + sanitize an LLM draft payload into a ProductBlueprint.

    Fail-closed on structure: malformed payloads raise (caller falls back).
    Fail-safe on content: block ids are filtered against the dual registry.
    """
    if not isinstance(data, dict):
        raise ValueError("LLM draft is not a JSON object")

    dual = set(dual_ids)
    raw_caps = data.get("capabilities")
    if not isinstance(raw_caps, list) or not raw_caps:
        raise ValueError("LLM draft has no capabilities")

    caps = []
    for item in raw_caps[:8]:
        if not isinstance(item, dict):
            continue
        cap_id = _slug(str(item.get("id", "")), default="")
        if not cap_id:
            continue
        block_ids = [
            b for b in item.get("block_ids", []) if isinstance(b, str) and b in dual
        ]
        caps.append(
            {
                "id": cap_id.replace("-", "_"),
                "description": str(item.get("description", ""))[:300]
                or f"Capability {cap_id}",
                "block_ids": block_ids,
                "strategy_hint": "REUSE" if block_ids else "GENERATE",
            }
        )
    if not caps:
        raise ValueError("LLM draft produced no usable capabilities")

    vertical = _snake_slug(str(data.get("vertical") or vertical_hint or "product"))
    product_name = str(data.get("product_name", "")).strip()[:120] or vertical.replace(
        "_", " "
    ).title()

    raw = {
        "schema_version": "product_blueprint.v1",
        "product_id": _slug(vertical),
        "product_name": product_name,
        "vertical": vertical,
        "summary": str(data.get("summary", "")).strip()[:500]
        or brief.strip()[:500]
        or f"Factory-drafted {vertical} product",
        "factory_scenario": FactoryScenario.CREATE_PRODUCT.value,
        "capabilities": caps,
        "ui_modules": ["command_center", "operational_chat", "resident_engineer"],
        "connectors": [],
        "edge_profile": "standard",
        "human_authority": True,
    }
    return ProductBlueprint.model_validate(raw)


def _draft_with_llm(
    brief: str,
    *,
    vertical_hint: Optional[str] = None,
) -> ProductBlueprint:
    """Draft a blueprint via the configured LLM. Raises on any failure."""
    dual = sorted(dual_registered_ids())
    block_list = "\n".join(f"- {b}" for b in dual) or "- (none registered)"
    messages = [
        {
            "role": "system",
            "content": _LLM_DRAFT_SYSTEM + f"\nAVAILABLE BLOCKS:\n{block_list}\n",
        },
        {"role": "user", "content": brief},
    ]
    data = _llm_json_call(messages)
    return _blueprint_from_llm_payload(data, brief, vertical_hint, dual)


# --- Deterministic keyword drafting (offline / canned demo path) --------------

_VERTICAL_FILLER = {
    "a", "an", "the", "me", "my", "our", "your", "us", "we", "i",
    "build", "create", "make", "generate", "assemble", "design", "ship",
    "new", "own", "custom", "secure", "multi", "user", "users", "multiuser",
    "for", "with", "and", "that", "to", "please", "business", "company",
    "app", "application", "tool", "solution", "service",
}
_VERTICAL_GERUNDS = {"managing", "tracking", "running", "handling", "monitoring"}


def _normalize_brief(text: str) -> str:
    """Normalize common noisy phrases before vertical extraction."""
    text = (text or "").lower()
    # "multi users" / "multi user" / "multi-user" → single token
    text = re.sub(r"\bmulti[-\s]?users?\b", "multi_user", text)
    return text


def _vertical_from_brief(text: str) -> str:
    """Deterministic vertical extraction from the brief (offline demo quality).

    Tries, in order:
      1. the descriptor preceding "platform/product/system/portal" (e.g.
         "inventory management platform" → inventory_management).
      2. the domain following "platform/product/system/portal for …" (e.g.
         "platform for my retail business" → retail, when the descriptor is
         only filler words like "secure multi users").
    Falls back to "product".
    """
    text = _normalize_brief(text)

    def _extract_words(phrase: str) -> list[str]:
        return [
            w
            for w in re.split(r"[\s\-]+", phrase)
            if w and w not in _VERTICAL_FILLER and w not in _VERTICAL_GERUNDS
        ]

    # 1) Descriptor before platform
    m = re.search(
        r"([a-z0-9][a-z0-9\s\-]{1,60}?)\s+(?:platform|product|system|portal)\b",
        text,
    )
    phrase = m.group(1) if m else ""
    words = _extract_words(phrase)

    # 2) If the descriptor is all filler, try "platform for [domain]"
    if not words:
        m2 = re.search(
            r"\b(?:platform|product|system|portal)\s+(?:for|that|to)\s+"
            r"([a-z0-9][a-z0-9\s\-]{1,60}?)(?:\s+(?:with|using|and)\b|$)",
            text,
        )
        phrase = m2.group(1) if m2 else ""
        words = _extract_words(phrase)

    return "_".join(words[:3]) or "product"


# --- Public drafting API ------------------------------------------------------


def draft_blueprint_from_brief(
    brief: str,
    *,
    vertical_hint: Optional[str] = None,
    use_golden_steward: bool = True,
    use_llm: Optional[bool] = None,
) -> ProductBlueprint:
    """Draft a ProductBlueprint from a user brief.

    Order of preference:
    1. LLM drafting when enabled (ARCHITECT_LLM_DRAFTING_ENABLED or
       ``use_llm=True``) — fail-safe: any error falls through to (2).
    2. Golden steward blueprint for explicit steward intent ("steward",
       "private estate", "property readiness", or vertical_hint == "estate")
       — deterministic fallback after LLM failure.
    3. Deterministic keyword drafting (always works, no keys needed).
    """
    if use_llm is None:
        use_llm = llm_drafting_enabled()
    if use_llm:
        try:
            return _draft_with_llm(brief, vertical_hint=vertical_hint)
        except Exception as exc:
            logger.warning("LLM drafting failed, falling back: %s", exc)

    text = (brief or "").lower()
    wants_steward = any(
        k in text for k in ("steward", "private estate", "property readiness")
    )
    if use_golden_steward and (wants_steward or vertical_hint == "estate"):
        return load_blueprint(steward_golden_path())

    dual = sorted(dual_registered_ids())
    # Blocks the brief actually mentions become REUSE capabilities; audit is
    # always added (governance is cross-cutting) so the demo blueprint never
    # ships governance-less.
    mentioned = [b for b in dual if b.replace("_", " ") in text or b in text]
    if "audit" in dual and "audit" not in mentioned:
        mentioned.append("audit")

    vertical = (vertical_hint or _vertical_from_brief(text)).replace(" ", "_").lower()
    product_id = re.sub(r"[^a-z0-9-]+", "-", vertical)[:48].strip("-") or "product"
    product_name = (
        vertical.replace("_", " ").replace("-", " ").title() + " Platform"
    ).strip()
    caps: List[Dict[str, Any]] = [
        {
            "id": f"{vertical.replace('-', '_')}_core",
            "description": f"Core {vertical.replace('_', ' ')} workflows and data model",
            "block_ids": [],
            "strategy_hint": "GENERATE",
        }
    ]
    for bid in mentioned[:7]:
        caps.append(
            {
                "id": bid,
                "description": f"{bid.replace('_', ' ').title()} capability (reused from the block store)",
                "block_ids": [bid],
                "strategy_hint": "REUSE",
            }
        )

    raw = {
        "schema_version": "product_blueprint.v1",
        "product_id": product_id,
        "product_name": product_name,
        "vertical": vertical,
        "summary": brief.strip()[:500] or f"Factory-drafted {vertical} product",
        "factory_scenario": FactoryScenario.CREATE_PRODUCT.value,
        "capabilities": caps,
        "ui_modules": ["command_center", "operational_chat", "resident_engineer"],
        "connectors": [],
        "edge_profile": "standard",
        "human_authority": True,
    }
    return ProductBlueprint.model_validate(raw)


def plan_blueprint(
    blueprint: ProductBlueprint,
    *,
    blocks_root: Optional[Path] = None,
) -> ProductPlan:
    return CapabilityPlanner(blocks_root).plan(blueprint)


def generate_product(
    blueprint: ProductBlueprint,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    factory_root = _repo_root()
    blocks = Path(blocks_root) if blocks_root else None
    if blocks is None:
        env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
        if env:
            blocks = Path(env)
    gen = ProductGenerator(
        blueprint,
        blocks_root=blocks,
        factory_commit=git_head(factory_root),
        blocks_commit=git_head(blocks) if blocks else "unknown",
    )
    return gen.generate(output_dir)


def architect_pipeline(
    brief: str,
    output_dir: Path | str,
    *,
    vertical_hint: Optional[str] = None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Brief → blueprint → plan → generate. Fail closed on UNSUPPORTED."""
    try:
        bp = draft_blueprint_from_brief(brief, vertical_hint=vertical_hint)
        plan = plan_blueprint(bp, blocks_root=blocks_root)
        result = generate_product(bp, output_dir, blocks_root=blocks_root)
        return {
            "ok": True,
            "blueprint": bp.model_dump(mode="json"),
            "plan": plan.to_dict(),
            "generation": {
                "output_dir": result["output_dir"],
                "inputs_hash": result["inputs_hash"],
                "product_id": result["product_id"],
            },
        }
    except DualRegistryError as exc:
        return {"ok": False, "error": str(exc)}


def blueprint_to_yaml(bp: ProductBlueprint) -> str:
    return yaml.safe_dump(bp.model_dump(mode="json"), sort_keys=False)
