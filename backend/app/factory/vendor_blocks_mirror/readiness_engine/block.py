"""Estate block: readiness_engine.

Readiness gate that CAN fail. Input: a checklist of items (``{id, required}``)
plus a state map of item id -> value. The gate passes (``status == "ok"``)
only when every required item is satisfied; otherwise it fails with
``status == "error"`` listing the unmet items.

Satisfied means: boolean True; non-zero numbers; non-empty strings unless the
string is an explicit not-ready word (false/no/pending/open/failed/missing/
incomplete/not_ready/unsatisfied/n/a/...); non-empty containers. ``required``
defaults to True when omitted. An empty checklist passes vacuously.

The result envelope keeps the consumer contract:
``{"block_id": "readiness_engine", "status": "ok"|"error", "result": ...}``
with ``error``/``detail`` added on failure.
"""

from __future__ import annotations

from typing import Any, Dict, List

BLOCK_ID = "readiness_engine"

_FALSE_WORDS = {
    "", "false", "no", "none", "null", "0", "off",
    "pending", "not_ready", "not-ready", "incomplete", "open",
    "fail", "failed", "missing", "unsatisfied", "n/a", "na",
    "unmet", "blocked", "overdue",
}


def _satisfied(value: Any) -> bool:
    """Whether a state value counts as satisfied."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_WORDS
    return bool(value)


def _envelope(
    status: str,
    result: Any = None,
    error: str = "",
    detail: Any = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "block_id": BLOCK_ID,
        "status": status,
        "result": result if result is not None else {},
    }
    if status == "error":
        out["error"] = error
        out["detail"] = detail if detail is not None else {}
    return out


def _evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    checklist = payload.get("checklist")
    state = payload.get("state")
    if not isinstance(checklist, list):
        return _envelope(
            "error", error="checklist must be a list of {id, required} items",
            detail={"missing": "checklist"},
        )
    if not isinstance(state, dict):
        return _envelope(
            "error", error="state must be a map of item id -> value",
            detail={"missing": "state"},
        )

    satisfied: List[str] = []
    unmet: List[Dict[str, Any]] = []
    malformed: List[Dict[str, Any]] = []
    for index, item in enumerate(checklist):
        if not isinstance(item, dict) or not item.get("id"):
            malformed.append({"index": index, "item": item})
            continue
        item_id = str(item["id"])
        required = item.get("required", True)
        if required and not _satisfied(state.get(item_id)):
            unmet.append({"id": item_id, "required": bool(required)})
        else:
            satisfied.append(item_id)

    if malformed:
        return _envelope(
            "error",
            error=f"checklist item(s) missing id: {len(malformed)}",
            detail={"malformed_items": malformed},
        )

    result = {
        "ready": not unmet,
        "checked": len(satisfied) + len(unmet),
        "satisfied": satisfied,
        "unmet": [item["id"] for item in unmet],
    }
    if unmet:
        names = ", ".join(item["id"] for item in unmet)
        return _envelope(
            "error",
            error=f"readiness gate failed: unmet required items: {names}",
            detail={"unmet_items": unmet},
            result=result,
        )
    return _envelope("ok", result)


def run(**kwargs: Any) -> Dict[str, Any]:
    """Execute the readiness_engine block."""
    payload = kwargs.get("input", kwargs)
    if not isinstance(payload, dict):
        payload = {"checklist": payload}
    action = str(payload.get("action", "evaluate")).lower()
    try:
        if action in ("evaluate", "check", "gate"):
            return _evaluate(payload)
        return _envelope(
            "error", error=f"unknown action: {action}",
            detail={"action": action, "known": ["evaluate"]},
        )
    except Exception as exc:  # noqa: BLE001 - envelope must never crash consumers
        return _envelope("error", error=str(exc), detail={"type": type(exc).__name__})
