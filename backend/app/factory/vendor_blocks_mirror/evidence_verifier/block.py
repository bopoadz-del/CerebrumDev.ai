"""Estate block: evidence_verifier.

SHA-256 tamper evidence, modeled on the universal-kernel audit_evidence and
provenance_verification blocks but stdlib-only and self-contained.

- ``store`` (default action): hash content, keep a chained integrity record
  (digest, size, timestamp, previous-hash linkage) in a module-level store.
- ``verify``: recompute the digest over the stored content (and over any
  caller-supplied content) and fail with ``status == "error"`` on tampering,
  a broken hash chain, or an unknown record id.
- ``list``: enumerate stored integrity records (without raw content).

The result envelope keeps the consumer contract:
``{"block_id": "evidence_verifier", "status": "ok"|"error", "result": ...}``
with ``error``/``detail`` added on failure.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

BLOCK_ID = "evidence_verifier"
ZERO_HASH = "0" * 64

# Module-level integrity store: record id -> record. Records retain their
# original content so that tampering is detectable by recomputing the digest.
_records: Dict[str, Dict[str, Any]] = {}
_order: List[str] = []  # insertion order, used for hash-chain linkage


def _canonical(record: Dict[str, Any]) -> str:
    """Stable JSON serialization for hashing."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _as_bytes(content: Any) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return _canonical(content).encode("utf-8")


def _digest(content: Any) -> str:
    """SHA-256 hex digest of content."""
    return hashlib.sha256(_as_bytes(content)).hexdigest()


def reset_state() -> None:
    """Clear stored integrity records (tests only)."""
    _records.clear()
    _order.clear()


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


def _store(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Hash content and store a chained integrity record."""
    if "content" not in payload or payload.get("content") is None:
        return _envelope(
            "error", error="content is required to store evidence",
            detail={"missing": "content"},
        )
    content = payload["content"]
    record_id = payload.get("id")
    digest = _digest(content)
    if record_id is None:
        record_id = f"evt_{digest[:16]}"
    record_id = str(record_id)
    if record_id in _records:
        return _envelope(
            "error", error=f"integrity record already stored: {record_id}",
            detail={"id": record_id, "duplicate": True},
        )
    previous_hash = (
        _records[_order[-1]].get("record_hash", ZERO_HASH) if _order else ZERO_HASH
    )
    body = {
        "id": record_id,
        "algorithm": "sha256",
        "sha256": digest,
        "size": len(_as_bytes(content)),
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "previous_hash": previous_hash,
    }
    record = dict(body)
    record["record_hash"] = hashlib.sha256(
        _canonical(body).encode("utf-8")
    ).hexdigest()
    # Retained so verify() can detect in-place tampering of stored content.
    record["content"] = content
    _records[record_id] = record
    _order.append(record_id)
    return _envelope("ok", body)


def _verify(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a stored integrity record; fail on tampered content."""
    record_id = payload.get("id")
    supplied = "content" in payload
    supplied_content = payload.get("content")

    if record_id is None:
        if not supplied:
            return _envelope(
                "error", error="id or content is required to verify evidence",
                detail={"missing": "id or content"},
            )
        digest = _digest(supplied_content)
        record_id = None
        for rid in _order:
            if _records[rid].get("sha256") == digest:
                record_id = rid
                break
        if record_id is None:
            return _envelope(
                "error",
                error="no integrity record matches the provided content",
                detail={"sha256": digest},
            )

    record = _records.get(str(record_id))
    if record is None:
        return _envelope(
            "error", error=f"unknown integrity record: {record_id}",
            detail={"id": record_id},
        )
    record_id = str(record_id)

    # Recompute the digest over the stored content (tamper check).
    actual = _digest(record.get("content"))
    expected = record.get("sha256")
    if actual != expected:
        return _envelope(
            "error",
            error=(
                "integrity verification failed: content tampered "
                f"(expected sha256 {expected}, got {actual})"
            ),
            detail={"id": record_id, "expected": expected, "actual": actual,
                    "tampered": True},
            result={"verified": False, "id": record_id},
        )

    # Verify hash-chain linkage for the record.
    index = _order.index(record_id)
    prev = _records[_order[index - 1]]["record_hash"] if index > 0 else ZERO_HASH
    if record.get("previous_hash") != prev:
        return _envelope(
            "error",
            error=f"integrity verification failed: chain broken at {record_id}",
            detail={"id": record_id, "chain_broken": True},
            result={"verified": False, "id": record_id},
        )

    # If the caller supplied content, it must match the stored digest too.
    if supplied and _digest(supplied_content) != expected:
        return _envelope(
            "error",
            error=(
                f"integrity verification failed: provided content does not "
                f"match record {record_id}"
            ),
            detail={"id": record_id, "content_mismatch": True},
            result={"verified": False, "id": record_id},
        )

    return _envelope("ok", {"verified": True, "id": record_id, "sha256": expected})


def _list() -> Dict[str, Any]:
    records = [
        {k: v for k, v in _records[rid].items() if k != "content"}
        for rid in _order
    ]
    return _envelope("ok", {"records": records})


def run(**kwargs: Any) -> Dict[str, Any]:
    """Execute the evidence_verifier block."""
    payload = kwargs.get("input", kwargs)
    if not isinstance(payload, dict):
        payload = {"content": payload}
    action = str(payload.get("action", "store")).lower()
    try:
        if action in ("store", "hash"):
            return _store(payload)
        if action in ("verify", "check"):
            return _verify(payload)
        if action in ("list", "records"):
            return _list()
        return _envelope(
            "error", error=f"unknown action: {action}",
            detail={"action": action, "known": ["store", "verify", "list"]},
        )
    except Exception as exc:  # noqa: BLE001 - envelope must never crash consumers
        return _envelope("error", error=str(exc), detail={"type": type(exc).__name__})
