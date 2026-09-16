"""Estate block: estate_registry.

JSON-file-backed registry for private-estate property/asset records.

- ``create`` (default action; aliases ``register``/``put``): persist a record
  to ``<store_dir>/estate_registry.json``. Duplicate ids are rejected with
  ``status == "error"``.
- ``read`` (alias ``get``): read back the stored record for an id.
- ``list`` (alias ``all``): every stored record, sorted by id.

``store_dir`` is caller-provided (``input.store_dir`` or a top-level
``store_dir`` kwarg). Default: ``$CEREBRUM_ESTATE_REGISTRY_DIR``, else
``<system temp dir>/cerebrum_estate_registry``. Writes are atomic
(temp file + replace). Stdlib-only and self-contained.

The result envelope keeps the consumer contract:
``{"block_id": "estate_registry", "status": "ok"|"error", "result": ...}``
with ``error``/``detail`` added on failure.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BLOCK_ID = "estate_registry"
REGISTRY_FILENAME = "estate_registry.json"
ENV_STORE_DIR = "CEREBRUM_ESTATE_REGISTRY_DIR"


def _default_store_dir() -> Path:
    env = os.environ.get(ENV_STORE_DIR)
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "cerebrum_estate_registry"


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


def _registry_path(store_dir: Any) -> Path:
    return Path(store_dir).expanduser() / REGISTRY_FILENAME


def _load(store_dir: Any) -> Dict[str, Any]:
    """Load the registry file, returning an empty registry when absent."""
    path = _registry_path(store_dir)
    if not path.is_file():
        return {"schema": "cerebrum.estate_registry.v1", "records": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), dict):
        raise ValueError(f"registry file is corrupt: {path}")
    return raw


def _save(store_dir: Any, data: Dict[str, Any]) -> None:
    """Atomically persist the registry (temp file + os.replace)."""
    path = _registry_path(store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def _store_dir_of(payload: Dict[str, Any]) -> Path:
    return Path(payload.get("store_dir") or _default_store_dir())


def _record_from(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build the record from ``record=`` or from the flat input fields."""
    record = payload.get("record")
    if not isinstance(record, dict):
        record = {
            k: v for k, v in payload.items()
            if k not in ("action", "store_dir", "input")
        }
    record = dict(record)
    if "id" not in record and payload.get("id"):
        record["id"] = payload.get("id")
    return record


def _create(payload: Dict[str, Any]) -> Dict[str, Any]:
    store_dir = _store_dir_of(payload)
    record = _record_from(payload)
    record_id = record.get("id")
    if record_id is None or str(record_id).strip() == "":
        return _envelope(
            "error", error="record id is required",
            detail={"missing": "id"},
        )
    record["id"] = str(record_id)
    data = _load(store_dir)
    if record["id"] in data["records"]:
        return _envelope(
            "error", error=f"duplicate record id: {record['id']}",
            detail={"id": record["id"], "duplicate": True},
        )
    record.setdefault("registered_at", datetime.now(timezone.utc).isoformat())
    data["records"][record["id"]] = record
    _save(store_dir, data)
    return _envelope(
        "ok", {"record": record, "store_dir": str(store_dir)},
    )


def _read(payload: Dict[str, Any]) -> Dict[str, Any]:
    store_dir = _store_dir_of(payload)
    record_id = str(payload.get("id") or "").strip()
    if not record_id:
        return _envelope(
            "error", error="record id is required",
            detail={"missing": "id"},
        )
    data = _load(store_dir)
    record = data["records"].get(record_id)
    if record is None:
        return _envelope(
            "error", error=f"record not found: {record_id}",
            detail={"id": record_id, "store_dir": str(store_dir)},
        )
    return _envelope("ok", record)


def _list(payload: Dict[str, Any]) -> Dict[str, Any]:
    store_dir = _store_dir_of(payload)
    data = _load(store_dir)
    records = [data["records"][rid] for rid in sorted(data["records"])]
    return _envelope("ok", {"records": records})


def run(**kwargs: Any) -> Dict[str, Any]:
    """Execute the estate_registry block."""
    payload = kwargs.get("input", kwargs)
    if not isinstance(payload, dict):
        payload = {"record": {"id": str(payload)}}
    # The documented contract honours a top-level ``store_dir`` kwarg as
    # well as ``input.store_dir``; fold it in so callers using either
    # spelling get the same registry file.
    if isinstance(payload, dict) and isinstance(
        kwargs.get("store_dir"), (str, Path)
    ):
        payload = {**payload, "store_dir": str(kwargs["store_dir"])}
    action = str(payload.get("action", "create")).lower()
    try:
        if action in ("create", "register", "put"):
            return _create(payload)
        if action in ("read", "get"):
            return _read(payload)
        if action in ("list", "all"):
            return _list(payload)
        return _envelope(
            "error", error=f"unknown action: {action}",
            detail={"action": action, "known": ["create", "read", "list"]},
        )
    except Exception as exc:  # noqa: BLE001 - envelope must never crash consumers
        return _envelope("error", error=str(exc), detail={"type": type(exc).__name__})
