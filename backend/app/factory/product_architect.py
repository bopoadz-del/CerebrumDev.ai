"""Product architect: brief analysis and platform blueprint drafting.

Hybrid by design: deterministic pattern extraction first, LLM refinement
behind ARCHITECT_LLM_DRAFTING_ENABLED, deterministic fallback always
available. LLM output is structurally validated and merged onto the
deterministic base — never trusted blindly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml

from ..core.dual_registry import dual_registered_ids
from ..core.llm_config import get_factory_llm_config

logger = logging.getLogger(__name__)

_DEFAULT_BLUEPRINTS_DIR = Path(__file__).resolve().parents[3] / "blueprints"


def _blueprints_dir() -> Path:
    override = os.getenv("ARCHITECT_BLUEPRINTS_DIR")
    return Path(override) if override else _DEFAULT_BLUEPRINTS_DIR


class ProductArchitect:
    """Drafts product blueprints from briefs. Deterministic-first, LLM-optional."""

    def __init__(self, blueprints_dir: Optional[Path] = None) -> None:
        self._dir = blueprints_dir or _blueprints_dir()

    # -- public API ----------------------------------------------------------

    def analyze_brief(self, brief: str) -> Dict[str, Any]:
        """Backward-compatible brief analysis (deterministic)."""
        return self._deterministic_analysis(brief)

    def draft_blueprint(
        self, brief: str, existing_yaml: Optional[str] = None
    ) -> Dict[str, Any]:
        """Draft a product blueprint dict.

        Priority:
        1. Golden steward match (estate/platform briefs) → golden blueprint.
        2. LLM-drafted refinement (when enabled + configured) validated and
           merged onto the deterministic base.
        3. Deterministic fallback.
        """
        golden = self._try_golden_steward(brief)
        if golden is not None:
            return golden

        base = self._deterministic_blueprint(brief, existing_yaml)

        if not os.getenv("ARCHITECT_LLM_DRAFTING_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            base["source"] = "deterministic"
            return base

        cfg = get_factory_llm_config()
        if cfg.get("error"):
            logger.warning("LLM drafting disabled: %s", cfg["error"])
            base["source"] = "deterministic"
            return base

        try:
            llm_draft = asyncio.run(self._llm_draft(brief, base, cfg))
        except Exception as exc:  # honest fallback, never crash the flow
            logger.warning("LLM drafting failed, using deterministic base: %s", exc)
            base["source"] = "deterministic"
            return base

        merged = self._merge_validated(base, llm_draft)
        merged["source"] = "llm+deterministic"
        return merged

    # -- golden steward ------------------------------------------------------

    def _try_golden_steward(self, brief: str) -> Optional[Dict[str, Any]]:
        if "estate" not in brief.lower() and "platform" not in brief.lower():
            return None
        steward_path = self._dir / "steward_product.yaml"
        if not steward_path.exists():
            return None
        try:
            data = yaml.safe_load(steward_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("capabilities"):
                data["source"] = "golden_steward"
                return data
        except Exception as exc:
            logger.warning("Failed to load golden steward blueprint: %s", exc)
        return None

    # -- deterministic base --------------------------------------------------

    def _deterministic_analysis(self, brief: str) -> Dict[str, Any]:
        return {
            "vertical": _vertical_from_brief(brief),
            "mentioned_blocks": _extract_mentioned_blocks(brief),
            "summary": brief.strip()[:200],
        }

    def _deterministic_blueprint(
        self, brief: str, existing_yaml: Optional[str] = None
    ) -> Dict[str, Any]:
        analysis = self._deterministic_analysis(brief)
        mentioned = [b for b in analysis["mentioned_blocks"] if b in dual_registered_ids()]
        vertical = analysis["vertical"]
        capabilities: List[Dict[str, Any]] = []
        if mentioned:
            capabilities.append(
                {
                    "id": f"{vertical}_core",
                    "description": f"Core {vertical} capability from brief",
                    "block_ids": mentioned[:8],
                    "strategy_hint": "REUSE",
                }
            )
        # audit is always available and useful
        if "audit" in dual_registered_ids() and "audit" not in mentioned:
            capabilities.append(
                {
                    "id": "audit_trail",
                    "description": "Audit trail and compliance logging",
                    "block_ids": ["audit"],
                    "strategy_hint": "REUSE",
                }
            )
        return {
            "product_name": _product_name_from_brief(brief, vertical),
            "vertical": vertical,
            "brief_summary": analysis["summary"],
            "capabilities": capabilities or _keyword_fallback_caps(brief, vertical),
        }

    # -- LLM refinement ------------------------------------------------------

    async def _llm_draft(
        self, brief: str, base: Dict[str, Any], cfg: Dict[str, Any]
    ) -> Dict[str, Any]:
        registry = sorted(dual_registered_ids())
        system = (
            "You are a product architect for the CerebrumDev factory. "
            "Draft a product blueprint as JSON with keys: product_name (string), "
            "vertical (string), capabilities (list of objects with id, description, "
            "block_ids (list of strings), strategy_hint (one of REUSE, ADAPT, GENERATE)). "
            f"Only use block IDs from this registry: {registry}. "
            "Do not invent block IDs. Return only JSON."
        )
        user = (
            f"Brief: {brief}\n\n"
            f"Deterministic base draft for reference: {json.dumps(base, indent=2)}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        # Omit temperature unless explicitly configured: reasoning models
        # (kimi-k2.x) reject any explicit value other than 1.
        if cfg.get("temperature") is not None:
            payload["temperature"] = cfg["temperature"]
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{cfg['base_url'].rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        draft = json.loads(content)
        if not isinstance(draft, dict):
            raise ValueError("LLM draft is not a JSON object")
        return draft

    def _merge_validated(
        self, base: Dict[str, Any], llm_draft: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge LLM draft onto the deterministic base, validating structure.

        Only block_ids present in the dual registry survive. Invalid or
        missing fields fall back to the deterministic base values.
        """
        registry = dual_registered_ids()
        merged = dict(base)

        name = llm_draft.get("product_name")
        if isinstance(name, str) and name.strip():
            merged["product_name"] = name.strip()

        vertical = llm_draft.get("vertical")
        if isinstance(vertical, str) and vertical.strip():
            merged["vertical"] = _slugify(vertical)

        caps = llm_draft.get("capabilities")
        if isinstance(caps, list) and caps:
            clean_caps: List[Dict[str, Any]] = []
            for cap in caps:
                if not isinstance(cap, dict):
                    continue
                cap_id = cap.get("id")
                if not isinstance(cap_id, str) or not cap_id.strip():
                    continue
                raw_blocks = cap.get("block_ids")
                block_ids = (
                    [b for b in raw_blocks if isinstance(b, str) and b in registry]
                    if isinstance(raw_blocks, list)
                    else []
                )
                hint = cap.get("strategy_hint")
                clean_caps.append(
                    {
                        "id": _slugify(cap_id),
                        "description": str(cap.get("description") or ""),
                        "block_ids": block_ids,
                        "strategy_hint": hint if hint in ("REUSE", "ADAPT", "GENERATE") else "GENERATE",
                    }
                )
            if clean_caps:
                merged["capabilities"] = clean_caps

        return merged


# ---------------------------------------------------------------------------
# helpers

_FILLER = {
    "a", "an", "the", "me", "my", "our", "your", "us", "we", "i",
    "build", "create", "make", "generate", "assemble", "design", "ship",
    "new", "own", "custom", "secure", "multi", "user", "multiuser",
    "for", "with", "and", "that", "to", "please",
}
_GERUNDS = {"managing", "tracking", "running", "handling", "monitoring"}

_BLOCK_VOCAB = [
    "audit",
    "auth",
    "chat",
    "ocr",
    "pdf",
    "image",
    "vector_search",
    "memory",
    "knowledge",
    "llm_enhancer",
    "validation_pipeline",
    "formula_executor_v2",
]


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "product"


def _vertical_from_brief(text: str) -> str:
    m = re.search(
        r"([a-z0-9][a-z0-9\s\-]{1,60}?)\s+(?:platform|product|system|portal)\b", text
    )
    phrase = m.group(1) if m else ""
    if not phrase:
        m2 = re.search(
            r"\b(?:platform|product|system|portal)\s+(?:for|that|to)\s+([a-z0-9][a-z0-9\s\-]{1,60}?)(?:\s+(?:with|using|and)\b|$)",
            text,
        )
        phrase = m2.group(1) if m2 else ""
    words = [
        w
        for w in re.split(r"[\s\-]+", phrase)
        if w and w not in _FILLER and w not in _GERUNDS
    ]
    return "_".join(words[:3]) or "product"


def _product_name_from_brief(brief: str, vertical: str) -> str:
    words = [w.capitalize() for w in vertical.split("_") if w]
    return " ".join(words) or "Custom Product"


def _extract_mentioned_blocks(brief: str) -> List[str]:
    lowered = brief.lower()
    return [b for b in _BLOCK_VOCAB if b.replace("_", " ") in lowered or b in lowered]


def _keyword_fallback_caps(brief: str, vertical: str) -> List[Dict[str, Any]]:
    """Always produce at least one GENERATE capability plus audit when available."""
    caps: List[Dict[str, Any]] = [
        {
            "id": f"{vertical}_core",
            "description": brief.strip()[:300],
            "block_ids": [],
            "strategy_hint": "GENERATE",
        }
    ]
    if "audit" in dual_registered_ids():
        caps.append(
            {
                "id": "audit_trail",
                "description": "Audit trail and compliance logging",
                "block_ids": ["audit"],
                "strategy_hint": "REUSE",
            }
        )
    return caps
