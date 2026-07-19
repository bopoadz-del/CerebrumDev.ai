"""Factory-generated platform block: capture."""

from __future__ import annotations
from typing import Any, Dict


def run(**kwargs: Any) -> Dict[str, Any]:
    payload = kwargs.get("input", kwargs)
    return {
        "block_id": "capture",
        "status": "ok",
        "result": payload if isinstance(payload, dict) else {"value": payload},
    }
