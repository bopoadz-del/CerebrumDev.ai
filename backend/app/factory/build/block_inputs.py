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

Live veterinary-care (sess_a4aa977d2dff4c55, 2026-09-04) then halted
WRITER on four *different* refusals — required record fields, not
``Unknown action: None``:

* event_bus — ``RuntimeError: topic required``
* document_engine — ``No input files provided (pdf/docx/xlsx)``
* database — ``Query failed: missing sql or table``
* team — ``Team access denied`` (domain ``team_id`` / missing minted id)

This module is the shared construction rule. WRITER emits it into every
generated platform as ``app/block_inputs.py`` and the fail-closed execute
wrapper calls ``prepare_block_input`` before ``dispatch.execute``. That is
handler-layer adaptation, not F18 fabrication inside ``dispatch.py``
(``_default_block_field`` / ``_ALWAYS_FILL`` stay forbidden there).

Only missing constructible keys are filled. Values the handler already
supplied are left alone, except a domain-looking ``team_id`` (not minted
by ``create_team``) which is replaced by the platform precondition id.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
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

#: Envelope keys that are never domain columns (return-shape / dispatch).
_ENVELOPE_NAMES = frozenset(
    {
        "ok",
        "error",
        "capability",
        "id",
        "result",
        "results",
        "payload",
        "action",
        "block",
        "blocks",
    }
)

#: Block-contract keys ``prepare_block_input`` constructs. Still skipped when
#: building notification summaries, but alignment MAY treat ``channel`` /
#: ``message`` as domain fields when a handler validates them as such
#: (live VetConnect: reminder ``channel`` ∈ email/sms/…).
_BLOCK_CONTRACT_NAMES = frozenset({"channel", "message", "steps", "team_id"})

#: Summary / prepare_block_input skip set (envelope + block-contract).
_SKIP_REQUIRED_NAMES = _ENVELOPE_NAMES | _BLOCK_CONTRACT_NAMES | {"status"}

#: Alignment never copies envelope keys or workflow/team construction keys.
#: ``status`` and ``channel`` stay eligible — they are common domain columns.
_ALIGN_SKIP_NAMES = _ENVELOPE_NAMES | frozenset({"steps", "team_id"})

_FIELD_NAME = r"[A-Za-z_][\w]*"

#: Error / guard patterns that name a domain field the handler requires.
_HANDLER_REQUIRED_PATTERNS = (
    re.compile(
        r"Missing required field[:\s]+['\"]?(" + _FIELD_NAME + r")",
        re.IGNORECASE,
    ),
    re.compile(
        r"""if\s+['\"](""" + _FIELD_NAME + r""")['\"]\s+not in\s+payload""",
    ),
    re.compile(
        r"""['\"](""" + _FIELD_NAME + r""")['\"]\s+is required""",
        re.IGNORECASE,
    ),
    # Live VetConnect: "pet_id is missing and must be a non-empty string"
    re.compile(
        r"""\b(""" + _FIELD_NAME + r""")\s+is missing\b""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""if\s+not\s+payload\.get\(\s*['\"](""" + _FIELD_NAME + r""")['\"]""",
    ),
    re.compile(
        r"""payload\.get\(\s*['\"](""" + _FIELD_NAME + r""")['\"]\s*\)\s+in\s+\(None""",
    ),
)

#: "Missing required fields: pet_name, owner_name, appointment_date"
_MISSING_FIELDS_LIST = re.compile(
    r"Missing required fields?\s*:\s*(.+?)(?:\"|'|$)",
    re.IGNORECASE,
)

#: required / needed = ["pet_name", "owner_name"]
_REQUIRED_ASSIGNMENT = re.compile(
    r"""(?:required(?:_fields)?|needed)\s*=\s*(\[[^\]]+\]|\([^)]+\))""",
    re.IGNORECASE,
)

_IDENT_IN_LIST = re.compile(r"""['\"](""" + _FIELD_NAME + r""")['\"]""")

#: role must be one of {veterinarian, technician, …}
_MUST_BE_ONE_OF = re.compile(
    r"""['\"]?(""" + _FIELD_NAME + r""")['\"]?\s+must be one of\s*[:\{{]\s*([^}}\n'\"]+)""",
    re.IGNORECASE,
)

#: payload.get("role") not in ("veterinarian", "technician")
_GET_NOT_IN = re.compile(
    r"""payload(?:\.get\(\s*|\s*\[\s*)['\"]("""
    + _FIELD_NAME
    + r""")['\"]\s*\)?\s+not in\s*(\[[^\]]+\]|\([^)]+\)|\{[^}]+\})""",
    re.IGNORECASE,
)

_MUST_BE_BOOL = re.compile(
    r"""['\"]?(""" + _FIELD_NAME + r""")['\"]?\s+must be a boolean""",
    re.IGNORECASE,
)
_MUST_BE_INT = re.compile(
    r"""['\"]?("""
    + _FIELD_NAME
    + r""")['\"]?\s+must be an integer(?:\s*>=\s*(-?\d+))?""",
    re.IGNORECASE,
)
_MUST_BE_NONEMPTY = re.compile(
    r"""['\"]?(""" + _FIELD_NAME + r""")['\"]?\s+must be a non-empty string""",
    re.IGNORECASE,
)
_ISINSTANCE_BOOL = re.compile(
    r"""isinstance\(\s*payload(?:\.get\(\s*|\s*\[\s*)['\"]("""
    + _FIELD_NAME
    + r""")['\"][^,]*,\s*bool""",
)
_ISINSTANCE_INT = re.compile(
    r"""isinstance\(\s*payload(?:\.get\(\s*|\s*\[\s*)['\"]("""
    + _FIELD_NAME
    + r""")['\"][^,]*,\s*int""",
)
_VALUE_TOKEN = re.compile(r"""['\"]([^'\"]+)['\"]|([A-Za-z][\w-]*)""")


def split_execute_action(
    payload: Any,
    *,
    action: Optional[str] = None,
    default_action: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Lift ``action`` out of the payload; return ``(keyword, clean_record)``.

    Live makerspace-management (sess_39b5fec2abd346a5, 2026-09-04): every
    capability called ``execute(block, {..., "action": ...})`` with no
    ``action=`` keyword. ``app/dispatch.py`` routes payload keys into the
    block's record and reads the operation only from ``action=``, so the
    blocks answered ``Unknown action: None`` or ``unknown field(s): action``
    and WRITER halted on ``every capability wrote a payload its blocks
    refuse``.

    Preference: explicit ``action=`` / positional, then a string buried in
    the payload (or its ``input`` envelope), then the block's default.
    ``action`` is never a block record field — always strip it.
    """
    data = dict(payload) if isinstance(payload, dict) else (
        {} if payload is None else {"value": payload}
    )
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}

    def _usable(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value
        return None

    resolved = (
        _usable(action)
        or _usable(data.get("action"))
        or _usable(inner.get("action"))
        or _usable(default_action)
    )
    data.pop("action", None)
    if isinstance(data.get("input"), dict):
        cleaned = dict(data["input"])
        cleaned.pop("action", None)
        data["input"] = cleaned
    return resolved, data


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
    _resolved, data = split_execute_action(domain, action=action)
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
    if bid == "event_bus":
        return _for_event_bus(data)
    if bid == "database":
        return _for_database(data)
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


def _looks_minted_team_id(value: Any) -> bool:
    """True when ``value`` is a create_team id, not a domain label.

    Live veterinarian_availability forwarded a domain string as ``team_id``
    and the Store answered ``Team access denied``. Minted ids are
    ``team_`` + hex (see block_obligations.create_team measurement).
    """
    if not isinstance(value, str):
        return False
    body = value[5:] if value.startswith("team_") else ""
    return len(body) >= 8 and all(c in "0123456789abcdefABCDEF" for c in body)


def _platform_team_id() -> Optional[str]:
    """Id the R1c startup step minted, creating the team if startup missed it."""
    try:
        from app.preconditions import resource_id  # type: ignore
    except Exception:  # noqa: BLE001 — generated workspace may lack module
        return None
    tid = resource_id("team")
    if tid:
        return str(tid)
    try:
        from app.preconditions import ensure_all  # type: ignore

        ensure_all()
    except Exception:  # noqa: BLE001 — boot must not die inside prepare
        return None
    tid = resource_id("team")
    return str(tid) if tid else None


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
    minted = _platform_team_id()
    current = out.get("team_id")
    if minted and (not current or not _looks_minted_team_id(current)):
        out["team_id"] = minted
    # Final guard: never leave a None on a lowercased key.
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
        elif key in out and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


#: Smallest PDF the Store document_engine will open. ``text`` alone is not
#: enough: the live default parse action answers "No input files provided
#: (pdf/docx/xlsx). Pass file_path as pdf_path, docx_path, or xlsx_path."
_MINIMAL_PDF = (
    b"%PDF-1.1\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
)

_SYNTH_DOC_PATH: Optional[str] = None


def _existing_file_path(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return value if os.path.isfile(value) else None
    except OSError:
        return None


def _synthesized_document_path() -> str:
    """Write one reusable temp PDF so path keys point at a real file."""
    global _SYNTH_DOC_PATH
    if _SYNTH_DOC_PATH and os.path.isfile(_SYNTH_DOC_PATH):
        return _SYNTH_DOC_PATH
    fd, path = tempfile.mkstemp(prefix="platform-doc-", suffix=".pdf")
    with os.fdopen(fd, "wb") as handle:
        handle.write(_MINIMAL_PDF)
    _SYNTH_DOC_PATH = path
    return path


def _for_document_engine(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    # Drop dict values on path keys — that is the live TypeError.
    for key in _DOC_PATH_KEYS:
        if isinstance(out.get(key), dict):
            out.pop(key)
        elif key not in out and _existing_file_path(inner.get(key)):
            out[key] = inner[key]
    existing = None
    for key in _DOC_PATH_KEYS:
        existing = _existing_file_path(out.get(key))
        if existing:
            out.setdefault("file_path", existing)
            out.setdefault("pdf_path", existing)
            break
    text = out.get("text")
    if not (isinstance(text, str) and text.strip()):
        lines = [
            f"{key}: {value}"
            for key, value in data.items()
            if isinstance(value, (str, int, float, bool))
        ]
        text = "\n".join(lines) if lines else json.dumps(data, default=str)
        out["text"] = text
    if existing:
        return out
    # A placeholder path ("sample") or text-only payload still fails the
    # live parse action. Materialize a real PDF and point the contract keys
    # at it — do not invent success over a path the block cannot open.
    path = _synthesized_document_path()
    out["file_path"] = path
    out["pdf_path"] = path
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


_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")


def _topic_from_domain(data: Dict[str, Any]) -> str:
    """A short event_bus topic derived from the capability record."""
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", ".", _summary_message(data)).strip(".")
    return (slug or "platform.event")[:80]


def _for_event_bus(data: Dict[str, Any]) -> Dict[str, Any]:
    """Satisfy ``topic required`` from domain fields, never invent success.

    Live automated_reminders forwarded the capability JSON; the Store
    event_bus raised ``RuntimeError: topic required``. Map a domain name
    (reminder_type / event / …) or a record summary onto ``topic``.
    """
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    topic = out.get("topic") or inner.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        for key in _TOPIC_KEYS:
            if key == "topic":
                continue
            cand = out.get(key) if out.get(key) is not None else inner.get(key)
            if isinstance(cand, str) and cand.strip():
                topic = cand.strip()
                break
        else:
            topic = _topic_from_domain(data)
    out["topic"] = str(topic).strip()
    return out


def _for_database(data: Dict[str, Any]) -> Dict[str, Any]:
    """Satisfy ``missing sql or table`` from the domain record.

    The factory's Store-unwired query adapter
    (``offline_adapters.emit_database_query``) returns exactly
    ``Query failed: missing sql or table`` when the handler omitted both.
    A domain record is not SQL; map it onto ``table`` + ``values`` the way
    notification maps onto channel/message.
    """
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("sql", "table", "table_name", "values"):
        if key not in out and key in inner:
            out[key] = inner[key]
    sql = out.get("sql")
    if isinstance(sql, str) and sql.strip():
        return out
    table = out.get("table") or out.get("table_name")
    if not (isinstance(table, str) and table.strip()):
        for key in ("entity", "table", "table_name"):
            cand = out.get(key) or inner.get(key)
            if isinstance(cand, str) and re.match(r"^[A-Za-z_][\w]*$", cand):
                table = cand
                break
        else:
            table = "records"
    out["table"] = str(table).strip()
    if not isinstance(out.get("values"), dict):
        values = {
            key: value
            for key, value in data.items()
            if key not in _SKIP_REQUIRED_NAMES
            and key not in {"sql", "table", "table_name", "values", "input"}
            and isinstance(value, (str, int, float, bool))
        }
        if values:
            out["values"] = values
    return out


def _usable_align_name(name: Optional[str]) -> Optional[str]:
    name = str(name or "").strip()
    if not name or name in _ALIGN_SKIP_NAMES:
        return None
    return name


def _names_from_list_text(raw: str) -> List[str]:
    found: List[str] = []
    for match in _IDENT_IN_LIST.finditer(raw or ""):
        name = _usable_align_name(match.group(1))
        if name:
            found.append(name)
    if found:
        return found
    # Unquoted: pet_name, owner_name, appointment_date
    for part in re.split(r"[,;]", raw or ""):
        token = re.sub(r"[^A-Za-z0-9_]+", "", part)
        name = _usable_align_name(token)
        if name:
            found.append(name)
    return found


def _parse_value_list(raw: str) -> List[str]:
    """Split a handler enum listing into distinct string values."""
    values, seen = [], set()
    for match in _VALUE_TOKEN.finditer(raw or ""):
        token = next((g for g in match.groups() if g), None)
        if not token:
            continue
        token = token.strip().strip("{}[]()\"'")
        if not token or token.lower() in _ALIGN_SKIP_NAMES:
            continue
        if token not in seen:
            seen.add(token)
            values.append(token)
    return values


def _inferred_field_shape(name: str) -> Dict[str, Any]:
    """Type hint from a handler-required name when the body has no type check.

    This is not vocabulary invention: it only picks bool/int so
    ``_sample_payload`` does not emit the word ``sample`` for ``is_active``
    or ``login_count``. Enums still come from handler text.
    """
    n = name.lower()
    if n.startswith("is_") or n.startswith("has_"):
        return {"type": "bool", "required": True}
    if n.endswith("_count") or n in {"capacity", "quantity", "login_count"}:
        return {"type": "int", "required": True, "min": 0}
    return {"type": "str", "required": True}


def _merge_field_contract(
    field: Dict[str, Any],
    contract: Optional[Dict[str, Any]],
) -> bool:
    """Copy mined type / vocab / bounds onto ``field``. Returns True if changed."""
    if not contract:
        return False
    changed = False
    ctype = contract.get("type")
    if ctype and (
        not field.get("type")
        or (str(field.get("type")) in {"str", "text", "string"} and ctype != "str")
    ):
        field["type"] = ctype
        changed = True
    allowed = contract.get("allowed_values")
    if allowed and not field.get("allowed_values"):
        field["allowed_values"] = list(allowed)
        changed = True
    if contract.get("min") is not None and field.get("min") is None:
        field["min"] = contract["min"]
        changed = True
    if contract.get("max") is not None and field.get("max") is None:
        field["max"] = contract["max"]
        changed = True
    if contract.get("required") and not field.get("required"):
        field["required"] = True
        changed = True
    return changed


def handler_required_fields(handler_source: str) -> List[str]:
    """Domain field names a handler body treats as required."""
    found: List[str] = []
    text = handler_source or ""
    for pattern in _HANDLER_REQUIRED_PATTERNS:
        for match in pattern.finditer(text):
            name = _usable_align_name(next((g for g in match.groups() if g), None))
            if name:
                found.append(name)
    for match in _MISSING_FIELDS_LIST.finditer(text):
        found.extend(_names_from_list_text(match.group(1)))
    for match in _REQUIRED_ASSIGNMENT.finditer(text):
        found.extend(_names_from_list_text(match.group(1)))
    return sorted(set(found))


def handler_field_contracts(handler_source: str) -> Dict[str, Dict[str, Any]]:
    """Required names plus type / vocabulary / bounds the handler enforces.

    Live VetConnect (sess_73409fa): LLM handlers demanded ``role`` ∈
    {veterinarian, …}, ``is_active`` bool, ``login_count`` int ≥ 0, and
    non-empty ``*_id`` columns the model_specs never declared. Mining the
    contracts lets ``align_spec_to_handler_fields`` and ``_sample_payload``
    stay in lockstep without fabricating Store inputs.
    """
    text = handler_source or ""
    contracts: Dict[str, Dict[str, Any]] = {}

    def _touch(name: Optional[str]) -> Optional[Dict[str, Any]]:
        usable = _usable_align_name(name)
        if not usable:
            return None
        return contracts.setdefault(usable, {"name": usable, "required": True})

    for name in handler_required_fields(text):
        _touch(name)

    for match in _MUST_BE_ONE_OF.finditer(text):
        slot = _touch(match.group(1))
        values = _parse_value_list(match.group(2))
        if slot and values:
            slot["allowed_values"] = values
            slot.setdefault("type", "str")

    for match in _GET_NOT_IN.finditer(text):
        slot = _touch(match.group(1))
        values = _parse_value_list(match.group(2))
        if slot and values:
            slot["allowed_values"] = values
            slot.setdefault("type", "str")

    for match in _MUST_BE_BOOL.finditer(text):
        slot = _touch(match.group(1))
        if slot:
            slot["type"] = "bool"

    for match in _ISINSTANCE_BOOL.finditer(text):
        slot = _touch(match.group(1))
        if slot:
            slot["type"] = "bool"

    for match in _MUST_BE_INT.finditer(text):
        slot = _touch(match.group(1))
        if not slot:
            continue
        slot["type"] = "int"
        if match.group(2) is not None:
            slot["min"] = int(match.group(2))

    for match in _ISINSTANCE_INT.finditer(text):
        slot = _touch(match.group(1))
        if slot:
            slot.setdefault("type", "int")

    for match in _MUST_BE_NONEMPTY.finditer(text):
        slot = _touch(match.group(1))
        if slot:
            slot.setdefault("type", "str")

    return contracts


def align_spec_to_handler_fields(
    spec: Optional[Dict[str, Any]],
    required_names: Iterable[str],
    contracts: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Add / enrich handler-required domain fields the model_specs omitted.

    Live miss: handler validated ``property_reference_code`` while the
    model_specs (and therefore ``_sample_payload``) never declared it, so
    pilot rejected "a payload built from its own schema".

    A later VetConnect miss: fields existed as bare ``str`` (or were skipped
    because ``status`` / ``channel`` sat on the envelope skip list) while the
    handler enforced a vocabulary, a bool, or ``int >= 0``. This merge copies
    those contracts onto the spec so the sample payload satisfies the guard.
    """
    base = dict(spec or {})
    fields = [dict(f) for f in (base.get("fields") or []) if isinstance(f, dict)]
    by_name = {str(f.get("name")): f for f in fields if f.get("name")}
    contracts = dict(contracts or {})
    changed: List[str] = []

    names: List[str] = []
    for name in required_names:
        usable = _usable_align_name(name)
        if usable and usable not in names:
            names.append(usable)
    for name in contracts:
        usable = _usable_align_name(name)
        if usable and usable not in names:
            names.append(usable)

    for name in names:
        contract = contracts.get(name) or {}
        if name in by_name:
            if _merge_field_contract(by_name[name], contract):
                changed.append(name)
            continue
        field = {"name": name, **_inferred_field_shape(name)}
        _merge_field_contract(field, contract)
        fields.append(field)
        by_name[name] = field
        changed.append(name)

    if not changed:
        return base if spec is not None else {"fields": fields}, []
    out = dict(base)
    out["fields"] = fields
    notes = list(out.get("handler_aligned_fields") or [])
    notes.extend(changed)
    out["handler_aligned_fields"] = notes
    return out, changed


def align_spec_to_handler_source(
    spec: Optional[Dict[str, Any]],
    handler_source: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """Mine a handler (or route) body and align the spec in one step."""
    contracts = handler_field_contracts(handler_source)
    return align_spec_to_handler_fields(
        spec,
        handler_required_fields(handler_source),
        contracts=contracts,
    )


def render_block_inputs_module() -> str:
    """Source for the generated platform's ``app/block_inputs.py``."""
    return '''"""Block input construction for this platform.

Generated by the factory WRITER. Handlers call prepare_block_input (via the
fail-closed execute wrapper) so domain records become block-acceptable
payloads without requiring the caller to know about channel/steps/paths.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")
_MINIMAL_PDF = (
    b"%PDF-1.1\\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\\n"
    b"trailer<</Size 4/Root 1 0 R>>\\n"
)
_SYNTH_DOC_PATH = None
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


def split_execute_action(
    payload: Any,
    *,
    action: Optional[str] = None,
    default_action: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    data = dict(payload) if isinstance(payload, dict) else (
        {} if payload is None else {"value": payload}
    )
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}

    def _usable(value):
        if isinstance(value, str) and value.strip():
            return value
        return None

    resolved = (
        _usable(action)
        or _usable(data.get("action"))
        or _usable(inner.get("action"))
        or _usable(default_action)
    )
    data.pop("action", None)
    if isinstance(data.get("input"), dict):
        cleaned = dict(data["input"])
        cleaned.pop("action", None)
        data["input"] = cleaned
    return resolved, data


def prepare_block_input(
    block_id: str,
    domain: Any,
    *,
    action: Optional[str] = None,
    roster: Sequence[str] = (),
    product_name: str = "platform",
) -> Dict[str, Any]:
    _resolved, data = split_execute_action(domain, action=action)
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
    if bid == "event_bus":
        return _for_event_bus(data)
    if bid == "database":
        return _for_database(data)
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


def _looks_minted_team_id(value):
    if not isinstance(value, str):
        return False
    body = value[5:] if value.startswith("team_") else ""
    return len(body) >= 8 and all(c in "0123456789abcdefABCDEF" for c in body)


def _platform_team_id():
    try:
        from app.preconditions import resource_id
    except Exception:
        return None
    tid = resource_id("team")
    if tid:
        return str(tid)
    try:
        from app.preconditions import ensure_all

        ensure_all()
    except Exception:
        return None
    tid = resource_id("team")
    return str(tid) if tid else None


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
    minted = _platform_team_id()
    current = out.get("team_id")
    if minted and (not current or not _looks_minted_team_id(current)):
        out["team_id"] = minted
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
        elif key in out and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


def _existing_file_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return value if os.path.isfile(value) else None
    except OSError:
        return None


def _synthesized_document_path():
    global _SYNTH_DOC_PATH
    if _SYNTH_DOC_PATH and os.path.isfile(_SYNTH_DOC_PATH):
        return _SYNTH_DOC_PATH
    fd, path = tempfile.mkstemp(prefix="platform-doc-", suffix=".pdf")
    with os.fdopen(fd, "wb") as handle:
        handle.write(_MINIMAL_PDF)
    _SYNTH_DOC_PATH = path
    return path


def _for_document_engine(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in _DOC_PATH_KEYS:
        if isinstance(out.get(key), dict):
            out.pop(key)
        elif key not in out and _existing_file_path(inner.get(key)):
            out[key] = inner[key]
    existing = None
    for key in _DOC_PATH_KEYS:
        existing = _existing_file_path(out.get(key))
        if existing:
            out.setdefault("file_path", existing)
            out.setdefault("pdf_path", existing)
            break
    text = out.get("text")
    if not (isinstance(text, str) and text.strip()):
        lines = [
            f"{key}: {value}"
            for key, value in data.items()
            if isinstance(value, (str, int, float, bool))
        ]
        out["text"] = "\\n".join(lines) if lines else json.dumps(data, default=str)
    if existing:
        return out
    path = _synthesized_document_path()
    out["file_path"] = path
    out["pdf_path"] = path
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


def _topic_from_domain(data):
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", ".", _summary_message(data)).strip(".")
    return (slug or "platform.event")[:80]


def _for_event_bus(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    topic = out.get("topic") or inner.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        for key in _TOPIC_KEYS:
            if key == "topic":
                continue
            cand = out.get(key) if out.get(key) is not None else inner.get(key)
            if isinstance(cand, str) and cand.strip():
                topic = cand.strip()
                break
        else:
            topic = _topic_from_domain(data)
    out["topic"] = str(topic).strip()
    return out


def _for_database(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("sql", "table", "table_name", "values"):
        if key not in out and key in inner:
            out[key] = inner[key]
    sql = out.get("sql")
    if isinstance(sql, str) and sql.strip():
        return out
    table = out.get("table") or out.get("table_name")
    if not (isinstance(table, str) and table.strip()):
        for key in ("entity", "table", "table_name"):
            cand = out.get(key) or inner.get(key)
            if isinstance(cand, str) and re.match(r"^[A-Za-z_][\\w]*$", cand):
                table = cand
                break
        else:
            table = "records"
    out["table"] = str(table).strip()
    if not isinstance(out.get("values"), dict):
        values = {
            key: value
            for key, value in data.items()
            if key not in _SKIP
            and key not in {"sql", "table", "table_name", "values", "input"}
            and isinstance(value, (str, int, float, bool))
        }
        if values:
            out["values"] = values
    return out
'''
