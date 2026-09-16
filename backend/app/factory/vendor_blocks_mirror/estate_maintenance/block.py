"""Estate block: estate_maintenance.

Maintenance work orders kept in a module-level store:

- ``create`` (default action): create a work order from ``title`` (required)
  and ``due`` (optional; orders without a due date sort last).
- ``list``: all work orders sorted by due date (earliest first, None last).
- ``complete``: mark a work order completed; an unknown id fails with
  ``status == "error"``.

The result envelope keeps the consumer contract:
``{"block_id": "estate_maintenance", "status": "ok"|"error", "result": ...}``
with ``error``/``detail`` added on failure.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

BLOCK_ID = "estate_maintenance"

# Module-level work-order store: order id -> order.
_orders: Dict[str, Dict[str, Any]] = {}


def reset_state() -> None:
    """Clear stored work orders (tests only)."""
    _orders.clear()


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


def _create(payload: Dict[str, Any]) -> Dict[str, Any]:
    title = payload.get("title")
    if title is None or str(title).strip() == "":
        return _envelope(
            "error", error="title is required to create a work order",
            detail={"missing": "title"},
        )
    due = payload.get("due")
    order_id = str(payload.get("id") or uuid.uuid4().hex[:12])
    if order_id in _orders:
        return _envelope(
            "error", error=f"work order already exists: {order_id}",
            detail={"id": order_id, "duplicate": True},
        )
    order = {
        "id": order_id,
        "title": str(title),
        "due": due,
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _orders[order_id] = order
    return _envelope("ok", order)


def _list() -> Dict[str, Any]:
    orders = sorted(
        _orders.values(),
        key=lambda o: (
            o.get("due") is None,
            o.get("due") if o.get("due") is not None else "",
            o.get("created_at") or "",
        ),
    )
    return _envelope("ok", {"orders": list(orders)})


def _complete(payload: Dict[str, Any]) -> Dict[str, Any]:
    order_id = str(payload.get("id") or "").strip()
    if not order_id:
        return _envelope(
            "error", error="work order id is required",
            detail={"missing": "id"},
        )
    order = _orders.get(order_id)
    if order is None:
        return _envelope(
            "error", error=f"unknown work order id: {order_id}",
            detail={"id": order_id, "unknown": True},
        )
    if order.get("status") != "completed":
        order["status"] = "completed"
        order["completed_at"] = datetime.now(timezone.utc).isoformat()
    return _envelope("ok", order)


def run(**kwargs: Any) -> Dict[str, Any]:
    """Execute the estate_maintenance block."""
    payload = kwargs.get("input", kwargs)
    if not isinstance(payload, dict):
        payload = {"title": str(payload)}
    action = str(payload.get("action", "create")).lower()
    try:
        if action in ("create", "new"):
            return _create(payload)
        if action in ("list", "all"):
            return _list()
        if action in ("complete", "close", "finish"):
            return _complete(payload)
        return _envelope(
            "error", error=f"unknown action: {action}",
            detail={"action": action, "known": ["create", "list", "complete"]},
        )
    except Exception as exc:  # noqa: BLE001 - envelope must never crash consumers
        return _envelope("error", error=str(exc), detail={"type": type(exc).__name__})
