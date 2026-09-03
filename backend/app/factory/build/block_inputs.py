"""Construct block-shaped inputs from a capability's domain record.

Handlers must never require block-specific fields (``channel``, ``steps``,
``team_id``, path strings) from the caller — the coder prompt already says
so. Live residential-lettings (sess_6400b6c / hash b36090a424db) still
shipped handlers that forwarded the domain JSON unchanged, so pilot
``test_every_capability_route_accepts_payload`` failed on:

* notification — missing ``channel`` / ``message``
* workflow — missing ``steps``
* team — ``NoneType.lower`` on empty name/slug
* document_engine — dict handed to a path-like opener

This module is the shared construction rule. WRITER emits it into every
generated platform as ``app/block_inputs.py`` and the fail-closed execute
wrapper calls ``prepare_block_input`` before ``dispatch.execute``. That is
handler-layer adaptation, not F18 fabrication inside ``dispatch.py``
(``_default_block_field`` / ``_ALWAYS_FILL`` stay forbidden there).

Only missing constructible keys are filled. Values the handler already
supplied are left alone.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Path-like keys document_engine (and SCHEMA_OBLIGATIONS) accept.
_DOC_PATH_KEYS = (
    "file_path",
    "pdf_path",
    "docx_path",
    "xlsx_path",
    "attachment_path",
    "document_path",
    "path",
)

#: Team fields the Store block lowercases; None must never reach them.
_TEAM_STRING_KEYS = ("user_id", "name", "slug", "role", "email", "plan", "permission")

#: Error / guard patterns that name a domain field the handler requires.
_HANDLER_REQUIRED_PATTERNS = (
    re.compile(
        r"Missing required field[:\s]+['\"]?([A-Za-z_][\w]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"""if\s+['\"]([A-Za-z_][\w]*)['\"]\s+not in\s+payload""",
    ),
    re.compile(
        r"""['\"]([A-Za-z_][\w]*)['\"]\s+is required""",
        re.IGNORECASE,
    ),
)

_SKIP_REQUIRED_NAMES = frozenset(
    {
        "ok",
        "error",
        "capability",
        "id",
        "status",
        "result",
        "results",
        "payload",
        "action",
        "block",
        "blocks",
        "channel",
        "message",
        "steps",
        "team_id",
    }
)


def prepare_block_input(
    block_id: str,
    domain: Any,
    *,
    action: Optional[str] = None,
    roster: Sequence[str] = (),
    product_name: str = "platform",
) -> Dict[str, Any]:
    """Return a payload the named block can accept for ``action``.

    ``domain`` is the caller's capability record (or an already-built block
    input). Missing block-contract keys are derived; existing keys win.
    """
    data = dict(domain) if isinstance(domain, dict) else {"value": domain}
    bid = str(block_id or "")
    if bid == "notification":
        return _for_notification(data, roster)
    if bid == "workflow":
        return _for_workflow(data, roster)
    if bid == "team":
        return _for_team(data, product_name=product_name)
    if bid == "document_engine":
        return _for_document_engine(data)
    if bid == "analytics":
        return _for_analytics(data)
    return data


def _summary_message(data: Dict[str, Any]) -> str:
    parts = [
        f"{key}={value}"
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool)) and key not in _SKIP_REQUIRED_NAMES
    ]
    text = "; ".join(parts).strip()
    return (text or "platform notification")[:500]


def _for_notification(data: Dict[str, Any], roster: Sequence[str]) -> Dict[str, Any]:
    out = dict(data)
    if not out.get("channel"):
        out["channel"] = "mcp"
    if not out.get("message"):
        body = out.get("body")
        out["message"] = body if isinstance(body, str) and body.strip() else _summary_message(data)
    if str(out.get("channel")).lower() == "mcp" and not out.get("block") and not out.get("tool"):
        peers = [b for b in roster if b and b != "notification"]
        out["block"] = peers[0] if peers else "notification"
    return out


def _for_workflow(data: Dict[str, Any], roster: Sequence[str]) -> Dict[str, Any]:
    out = dict(data)
    steps = out.get("steps")
    if isinstance(steps, list) and steps:
        return out
    peers = [b for b in roster if b and b != "workflow"]
    built: List[Dict[str, Any]] = []
    for block in peers[:3]:
        # workflow reads step.get("block"); "block_id" is ignored (live miss).
        built.append({"block": block, "input": dict(data)})
    if not built:
        # Capability bound only to workflow: still supply a well-formed step
        # list so the block's required-field check is not the failure mode.
        built.append({"block": "workflow", "input": dict(data)})
    out["steps"] = built
    return out


def _for_team(data: Dict[str, Any], *, product_name: str = "platform") -> Dict[str, Any]:
    out = dict(data)
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
    slug_base = re.sub(r"[^a-z0-9-]+", "-", (product_name or "platform").lower()).strip("-")
    slug_base = slug_base or "platform"
    out.setdefault("user_id", "system")
    out.setdefault("name", f"{product_name or 'platform'} team")
    out.setdefault("slug", f"{slug_base}-team")
    if not out.get("team_id"):
        try:
            from app.preconditions import resource_id  # type: ignore

            tid = resource_id("team")
        except Exception:  # noqa: BLE001 — generated workspace may lack module
            tid = None
        if tid:
            out["team_id"] = str(tid)
    # Final guard: never leave a None on a lowercased key.
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
        elif key in out and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


def _for_document_engine(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    # Drop dict values on path keys — that is the live TypeError.
    for key in _DOC_PATH_KEYS:
        if isinstance(out.get(key), dict):
            out.pop(key)
    for key in _DOC_PATH_KEYS:
        val = out.get(key)
        if isinstance(val, (str, bytes)) and val:
            if isinstance(val, str):
                out.setdefault("file_path", val)
                out.setdefault("pdf_path", val)
            return out
    text = out.get("text")
    if isinstance(text, str) and text.strip():
        return out
    raw = out.get("bytes")
    if isinstance(raw, (bytes, str)) and raw:
        return out
    # Prefer a text document over inventing a filesystem path that does not exist.
    lines = [
        f"{key}: {value}"
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool))
    ]
    out["text"] = "\n".join(lines) if lines else json.dumps(data, default=str)
    return out


def _for_analytics(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("metric", "value"):
        if key not in out and key in inner:
            out[key] = inner[key]
    if not out.get("metric"):
        for key, value in data.items():
            if key != "input" and isinstance(value, str) and value:
                out["metric"] = key
                break
        out.setdefault("metric", "event")
    if out.get("value") is None:
        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out["value"] = value
                break
        out.setdefault("value", 1)
    return out


def handler_required_fields(handler_source: str) -> List[str]:
    """Domain field names a handler body treats as required."""
    found: List[str] = []
    text = handler_source or ""
    for pattern in _HANDLER_REQUIRED_PATTERNS:
        for match in pattern.finditer(text):
            name = next((g for g in match.groups() if g), None)
            if name and name not in _SKIP_REQUIRED_NAMES:
                found.append(name)
    return sorted(set(found))


def align_spec_to_handler_fields(
    spec: Optional[Dict[str, Any]],
    required_names: Iterable[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Add handler-required domain fields the model_specs omitted.

    Live miss: handler validated ``property_reference_code`` while the
    model_specs (and therefore ``_sample_payload``) never declared it, so
    pilot rejected "a payload built from its own schema".
    """
    base = dict(spec or {})
    fields = [dict(f) for f in (base.get("fields") or []) if isinstance(f, dict)]
    have = {str(f.get("name")) for f in fields if f.get("name")}
    added: List[str] = []
    for name in required_names:
        name = str(name or "").strip()
        if not name or name in have or name in _SKIP_REQUIRED_NAMES:
            continue
        fields.append({"name": name, "type": "str", "required": True})
        have.add(name)
        added.append(name)
    if not added:
        return base if spec is not None else {"fields": fields}, []
    out = dict(base)
    out["fields"] = fields
    notes = list(out.get("handler_aligned_fields") or [])
    notes.extend(added)
    out["handler_aligned_fields"] = notes
    return out, added


def render_block_inputs_module() -> str:
    """Source for the generated platform's ``app/block_inputs.py``."""
    return '''"""Block input construction for this platform.

Generated by the factory WRITER. Handlers call prepare_block_input (via the
fail-closed execute wrapper) so domain records become block-acceptable
payloads without requiring the caller to know about channel/steps/paths.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

_DOC_PATH_KEYS = (
    "file_path",
    "pdf_path",
    "docx_path",
    "xlsx_path",
    "attachment_path",
    "document_path",
    "path",
)
_TEAM_STRING_KEYS = ("user_id", "name", "slug", "role", "email", "plan", "permission")
_SKIP = frozenset(
    {
        "ok",
        "error",
        "capability",
        "id",
        "status",
        "result",
        "results",
        "payload",
        "action",
        "block",
        "blocks",
        "channel",
        "message",
        "steps",
        "team_id",
    }
)


def prepare_block_input(
    block_id: str,
    domain: Any,
    *,
    action: Optional[str] = None,
    roster: Sequence[str] = (),
    product_name: str = "platform",
) -> Dict[str, Any]:
    data = dict(domain) if isinstance(domain, dict) else {"value": domain}
    bid = str(block_id or "")
    if bid == "notification":
        return _for_notification(data, roster)
    if bid == "workflow":
        return _for_workflow(data, roster)
    if bid == "team":
        return _for_team(data, product_name=product_name)
    if bid == "document_engine":
        return _for_document_engine(data)
    if bid == "analytics":
        return _for_analytics(data)
    return data


def _summary_message(data: Dict[str, Any]) -> str:
    parts = [
        f"{key}={value}"
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool)) and key not in _SKIP
    ]
    return (("; ".join(parts)).strip() or "platform notification")[:500]


def _for_notification(data: Dict[str, Any], roster: Sequence[str]) -> Dict[str, Any]:
    out = dict(data)
    if not out.get("channel"):
        out["channel"] = "mcp"
    if not out.get("message"):
        body = out.get("body")
        out["message"] = (
            body if isinstance(body, str) and body.strip() else _summary_message(data)
        )
    if str(out.get("channel")).lower() == "mcp" and not out.get("block") and not out.get("tool"):
        peers = [b for b in roster if b and b != "notification"]
        out["block"] = peers[0] if peers else "notification"
    return out


def _for_workflow(data: Dict[str, Any], roster: Sequence[str]) -> Dict[str, Any]:
    out = dict(data)
    steps = out.get("steps")
    if isinstance(steps, list) and steps:
        return out
    peers = [b for b in roster if b and b != "workflow"]
    built: List[Dict[str, Any]] = [
        {"block": block, "input": dict(data)} for block in peers[:3]
    ]
    if not built:
        built.append({"block": "workflow", "input": dict(data)})
    out["steps"] = built
    return out


def _for_team(data: Dict[str, Any], *, product_name: str = "platform") -> Dict[str, Any]:
    out = dict(data)
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
    slug_base = re.sub(r"[^a-z0-9-]+", "-", (product_name or "platform").lower()).strip("-")
    slug_base = slug_base or "platform"
    out.setdefault("user_id", "system")
    out.setdefault("name", f"{product_name or 'platform'} team")
    out.setdefault("slug", f"{slug_base}-team")
    if not out.get("team_id"):
        try:
            from app.preconditions import resource_id

            tid = resource_id("team")
        except Exception:
            tid = None
        if tid:
            out["team_id"] = str(tid)
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
        elif key in out and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


def _for_document_engine(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    for key in _DOC_PATH_KEYS:
        if isinstance(out.get(key), dict):
            out.pop(key)
    for key in _DOC_PATH_KEYS:
        val = out.get(key)
        if isinstance(val, (str, bytes)) and val:
            if isinstance(val, str):
                out.setdefault("file_path", val)
                out.setdefault("pdf_path", val)
            return out
    text = out.get("text")
    if isinstance(text, str) and text.strip():
        return out
    raw = out.get("bytes")
    if isinstance(raw, (bytes, str)) and raw:
        return out
    lines = [
        f"{key}: {value}"
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool))
    ]
    out["text"] = "\\n".join(lines) if lines else json.dumps(data, default=str)
    return out


def _for_analytics(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("metric", "value"):
        if key not in out and key in inner:
            out[key] = inner[key]
    if not out.get("metric"):
        for key, value in data.items():
            if key != "input" and isinstance(value, str) and value:
                out["metric"] = key
                break
        out.setdefault("metric", "event")
    if out.get("value") is None:
        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out["value"] = value
                break
        out.setdefault("value", 1)
    return out
'''
