"""STEP 0 REUSE lookup — Blocks registry HTTP, feature-detected.

Contract (Blocks #106, may still be merging):

    GET /v1/registry/blocks/{id}   — preferred
    GET /v1/registry/reuse/{id}    — fallback path

Always 200 when the surface exists. Body carries ``present`` or ``reuse``
as true|false, and optional ``reads`` / ``writes`` / ``never`` /
``acceptance``. Until main has it, this client feature-detects: 404,
connect errors, and an unset ``CEREBRUM_API_URL`` fall back to the local
dual-registry id set. The compiler never invents presence.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx

logger = logging.getLogger("cerebrumdev.factory.reuse_lookup")

BLOCKS_PATH = "/v1/registry/blocks/{id}"
REUSE_PATH = "/v1/registry/reuse/{id}"
PROBE_TIMEOUT_S = 3.0

#: Feature-detect cache. Blocks #106 may still be merging; one failed
#: probe means the surface is unavailable for the rest of the process.
_http_surface: Optional[bool] = None


@dataclass
class ReuseRecord:
    block_id: str
    present: bool
    source: str
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    never: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "present": self.present,
            "source": self.source,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "never": list(self.never),
            "acceptance": list(self.acceptance),
        }


def store_base_url() -> str:
    return (os.getenv("CEREBRUM_API_URL") or "").strip().rstrip("/")


def _auth_headers() -> Dict[str, str]:
    key = (os.getenv("CEREBRUM_API_KEY") or os.getenv("CEREBRUM_API_TOKEN") or "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def parse_reuse_body(block_id: str, data: Any, *, source: str) -> ReuseRecord:
    """Interpret a 200 body. Missing keys mean 'not present', never assumed."""
    payload = data if isinstance(data, dict) else {}
    flag = payload.get("present")
    if flag is None:
        flag = payload.get("reuse")
    present = bool(flag) if flag is not None else False
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    return ReuseRecord(
        block_id=block_id,
        present=present,
        source=source,
        reads=_as_str_list(payload.get("reads") or scope.get("reads")),
        writes=_as_str_list(payload.get("writes") or scope.get("writes")),
        never=_as_str_list(payload.get("never") or scope.get("never")),
        acceptance=_as_str_list(payload.get("acceptance") or scope.get("acceptance")),
        raw=dict(payload),
    )


def _get(url: str) -> Optional[httpx.Response]:
    try:
        return httpx.get(url, headers=_auth_headers(), timeout=PROBE_TIMEOUT_S)
    except (httpx.HTTPError, OSError) as exc:
        logger.info("reuse lookup unreachable at %s: %s", url, exc)
        return None


def lookup_reuse_http(block_id: str, *, base_url: Optional[str] = None) -> Optional[ReuseRecord]:
    """One id against the Blocks REUSE surface. None = surface unavailable."""
    global _http_surface
    if _http_surface is False:
        return None
    root = (base_url if base_url is not None else store_base_url()).rstrip("/")
    if not root or not str(block_id).strip():
        return None
    bid = str(block_id).strip()
    saw_connection = False
    for path, label in (
        (BLOCKS_PATH.format(id=bid), "registry/blocks"),
        (REUSE_PATH.format(id=bid), "registry/reuse"),
    ):
        resp = _get(root + path)
        if resp is None:
            continue
        saw_connection = True
        if resp.status_code == 404:
            continue
        if resp.status_code == 200:
            _http_surface = True
            try:
                body = resp.json()
            except ValueError:
                _http_surface = False
                return None
            return parse_reuse_body(bid, body, source=label)
        logger.info(
            "reuse lookup %s returned %s — treating surface as unavailable",
            path,
            resp.status_code,
        )
        _http_surface = False
        return None
    if not saw_connection:
        _http_surface = False
    return None


def lookup_reuse(
    block_id: str,
    *,
    local_ids: Optional[Iterable[str]] = None,
    base_url: Optional[str] = None,
    http_get=None,
) -> ReuseRecord:
    """HTTP first when the surface exists; else local dual-registry membership."""
    bid = str(block_id).strip()
    known = {str(x) for x in (local_ids or ()) if str(x).strip()}
    getter = http_get or lookup_reuse_http
    remote = getter(bid, base_url=base_url) if base_url or store_base_url() else None
    if remote is not None:
        return remote
    return ReuseRecord(
        block_id=bid,
        present=bid in known,
        source="local_dual_registry" if bid in known else "absent",
    )


def resolve_store_presence(
    claimed_ids: Iterable[str],
    *,
    local_ids: Optional[Iterable[str]] = None,
    base_url: Optional[str] = None,
    http_get=None,
) -> Dict[str, ReuseRecord]:
    """Map each claimed id to a ReuseRecord. Never invent presence."""
    out: Dict[str, ReuseRecord] = {}
    for bid in claimed_ids:
        name = str(bid).strip()
        if not name or name in out:
            continue
        out[name] = lookup_reuse(
            name, local_ids=local_ids, base_url=base_url, http_get=http_get
        )
    return out


def present_ids(records: Dict[str, ReuseRecord]) -> Set[str]:
    return {bid for bid, rec in records.items() if rec.present}
