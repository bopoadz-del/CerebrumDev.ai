"""REUSE keep-path schema-sample accept contract (C-BRIEF).

Photographed Floor after #345 (tip 666659a, sess_78483eaf7acd4219,
VetCare Hub): ModuleNotFoundError did not recur. WRITER reached 5/5
routes via FACTORY_CODE_CLI_BILLING + FACTORY_CODE_CLI_REUSE keep-path.
TESTER PRODUCT then failed after rework budget 3:

    tests/test_routes.py::test_every_capability_route_accepts_payload
    - patient_records_management: database: Unknown action;
      validation: Unknown action: None
    - appointment_scheduling: workflow: step_0 (event_bus): error
    - prescription_management: validation: Unknown action: None
    - billing_and_invoicing: analytics: Unknown action: None
    - client_communication_portal: team: Unknown action: None
    Also: schema sample refused (event_bus workflow step);
    accept-payload persisted nothing.

Cause (verified in this repo): ``emit_factory_grounded_reuse_keep_path``
wrote ``_handler_module(..., entity=name)`` with no ``default_actions``,
so ``BLOCK_DEFAULT_ACTIONS = {}``. Keep-path then staged those files.
``execute(block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id))``
passes ``action=None``. Store blocks answer ``Unknown action`` /
``Unknown action: None``. Workflow children built from the schema sample
drop ``step.action`` and PRODUCT reports ``step_0 (event_bus): error``.

This module is the compiler + emit + harvest + WRITER halt — not a
per-capability handle() micro-shot. Do not claim pilot_zip.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.persist_accept import persist_handler_rel, persist_workspace_root
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.workflow_accept import (
    EVENT_BUS_STEP_ACTION,
    PRODUCT_EVENT_BUS_STEP_0_HALT,
    PRODUCT_EVENT_BUS_STEP_CLASS,
)

PRODUCT_UNKNOWN_ACTION_HALT = "Unknown action"
PRODUCT_UNKNOWN_ACTION_NONE_HALT = "Unknown action: None"
REUSE_ACCEPT_CHECK = "reuse_accept"
WRITER_REUSE_ACCEPT_HALT = (
    "WRITER [check:reuse_accept] failed — REUSE handler cannot accept "
    "its own schema sample (Unknown action / action=None)"
)
REUSE_ACCEPT_MISS = "reuse/accept miss"

#: Photographed VetCare Hub REUSE roster after #345 (sess_78483eaf7acd4219).
LIVE_VETCARE_REUSE_ACCEPT_CAPS = (
    "patient_records_management",
    "appointment_scheduling",
    "prescription_management",
    "billing_and_invoicing",
    "client_communication_portal",
)

LIVE_VETCARE_REUSE_ACCEPT_BLOCKS: Dict[str, List[str]] = {
    "patient_records_management": ["database", "validation"],
    "appointment_scheduling": ["event_bus", "workflow"],
    "prescription_management": ["validation"],
    "billing_and_invoicing": ["analytics"],
    "client_communication_portal": ["team"],
}

#: Factory-known Store defaults already documented in this repo
#: (LIVE_CONTRACTS, workflow_accept, writer_behaviour / contract probes).
#: Harvest from vendored block.json / source wins when present.
STORE_BLOCK_DEFAULT_ACTIONS: Dict[str, str] = {
    "analytics": "track_event",
    "audit": "log",
    "dashboard": "render",
    "database": "query",
    "document_engine": "parse",
    "event_bus": EVENT_BUS_STEP_ACTION,
    "notification": "send",
    "queue": "enqueue",
    "team": "create_team",
    "validation": "validate",
    "workflow": "run",
}

_BLOCK_DEFAULTS_ASSIGN = re.compile(
    r"BLOCK_DEFAULT_ACTIONS\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\})",
    re.MULTILINE,
)
_BLOCK_IDS_ASSIGN = re.compile(
    r"BLOCK_IDS\s*=\s*(\[(?:[^\[\]]|\[[^\[\]]*\])*\])",
    re.MULTILINE,
)
_ACTION_EQ = re.compile(
    r"""action\s*==\s*['\"]([A-Za-z_][\w]*)['\"]"""
    r"""|['\"]([A-Za-z_][\w]*)['\"]\s*==\s*action"""
)
_ACTION_NOT_IN = re.compile(
    r"action\s+not in\s*(\[[^\]]+\]|\([^)]+\))",
    re.IGNORECASE,
)
_IDENT_IN_LIST = re.compile(r"""['\"]([A-Za-z_][\w]*)['\"]""")


class ReuseAcceptHalt(ValueError):
    """WRITER must not claim done: REUSE schema-sample would Unknown action."""


def default_block_action(
    block_id: str,
    default_actions: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Keyword action for ``execute(..., action=)``. Never invent from payload."""
    bid = str(block_id or "").strip()
    if isinstance(default_actions, Mapping):
        cand = default_actions.get(bid)
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    mapped = STORE_BLOCK_DEFAULT_ACTIONS.get(bid)
    if isinstance(mapped, str) and mapped.strip():
        return mapped.strip()
    return None


def default_action_from_block_json(meta: Any) -> Optional[str]:
    """``inputs[].name == action`` default, else first option."""
    if not isinstance(meta, Mapping):
        return None
    for item in meta.get("inputs") or ():
        if not isinstance(item, Mapping):
            continue
        if str(item.get("name") or "") != "action":
            continue
        raw = item.get("default")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        options = item.get("options") or ()
        if isinstance(options, (list, tuple)):
            for opt in options:
                if isinstance(opt, str) and opt.strip():
                    return opt.strip()
    return None


def default_action_from_source(source: str) -> Optional[str]:
    """First action the vendored module compares against."""
    blob = source or ""
    match = _ACTION_NOT_IN.search(blob)
    if match:
        idents = _IDENT_IN_LIST.findall(match.group(1) or "")
        if idents:
            return idents[0]
    match = _ACTION_EQ.search(blob)
    if match:
        return (match.group(1) or match.group(2) or "").strip() or None
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    raw = _read_text(path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _workspace_roots(
    *roots: Any,
    workspace: Any = None,
) -> List[Path]:
    out: List[Path] = []
    if workspace is not None:
        for attr in ("workspace", "destination", "store_root"):
            raw = getattr(workspace, attr, None)
            if raw:
                out.append(Path(raw))
        if not out:
            out.append(persist_workspace_root(workspace))
    for root in roots:
        if root is None:
            continue
        out.append(Path(getattr(root, "workspace", root)))
    seen: set[str] = set()
    unique: List[Path] = []
    for path in out:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def harvest_block_default_action(
    block_id: str,
    *roots: Any,
    workspace: Any = None,
) -> Optional[str]:
    """Vendored block.json / source, then the factory-known Store map."""
    bid = str(block_id or "").strip()
    if not bid:
        return None
    if workspace is not None and getattr(workspace, "exists", None):
        meta_rel = Path("vendor") / "blocks" / bid / "block.json"
        if workspace.exists(meta_rel):
            try:
                meta = json.loads(workspace.read_text(meta_rel))
            except (ValueError, OSError, TypeError):
                meta = None
            harvested = default_action_from_block_json(meta)
            if harvested:
                return harvested
        for rel in (
            Path("vendor") / "blocks" / bid / "block.py",
            Path("vendor") / "cerebrum" / "blocks" / f"{bid}.py",
        ):
            if workspace.exists(rel):
                harvested = default_action_from_source(workspace.read_text(rel))
                if harvested:
                    return harvested
    for root in _workspace_roots(*roots, workspace=workspace):
        meta = _load_json(root / "vendor" / "blocks" / bid / "block.json")
        harvested = default_action_from_block_json(meta)
        if harvested:
            return harvested
        for rel in (
            Path("vendor") / "blocks" / bid / "block.py",
            Path("vendor") / "cerebrum" / "blocks" / f"{bid}.py",
        ):
            harvested = default_action_from_source(_read_text(root / rel))
            if harvested:
                return harvested
    return default_block_action(bid)


def harvest_block_default_actions(
    block_ids: Sequence[str],
    *roots: Any,
    workspace: Any = None,
) -> Dict[str, str]:
    """capability BLOCK_IDS → keyword actions for keep-path emit."""
    out: Dict[str, str] = {}
    for raw in block_ids or ():
        bid = str(raw or "").strip()
        if not bid:
            continue
        action = harvest_block_default_action(bid, *roots, workspace=workspace)
        if action:
            out[bid] = action
    return out


def parse_handler_block_ids(text: str) -> List[str]:
    match = _BLOCK_IDS_ASSIGN.search(text or "")
    if not match:
        return []
    try:
        value = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def parse_handler_default_actions(text: str) -> Dict[str, str]:
    match = _BLOCK_DEFAULTS_ASSIGN.search(text or "")
    if not match:
        return {}
    try:
        value = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(val)
        for key, val in value.items()
        if str(key).strip() and isinstance(val, str) and val.strip()
    }


def apply_default_actions_to_handler(
    text: str, default_actions: Mapping[str, str]
) -> str:
    """Rewrite ``BLOCK_DEFAULT_ACTIONS = …`` so keep-path source is honest."""
    blob = text or ""
    assignment = f"BLOCK_DEFAULT_ACTIONS = {dict(default_actions or {})!r}"
    if _BLOCK_DEFAULTS_ASSIGN.search(blob):
        return _BLOCK_DEFAULTS_ASSIGN.sub(assignment, blob, count=1)
    ids_match = _BLOCK_IDS_ASSIGN.search(blob)
    if ids_match:
        insert_at = ids_match.end()
        return blob[:insert_at] + "\n" + assignment + blob[insert_at:]
    return assignment + "\n" + blob


def reuse_accept_handler_errors(
    text: str,
    block_ids: Optional[Sequence[str]] = None,
    *,
    capability_id: str = "",
) -> List[str]:
    """Empty = this handler can keyword-dispatch its bound blocks."""
    bids = [
        str(b).strip()
        for b in (block_ids if block_ids is not None else parse_handler_block_ids(text))
        if str(b).strip()
    ]
    if not bids:
        return []
    defaults = parse_handler_default_actions(text)
    errors: List[str] = []
    prefix = f"{capability_id}: " if capability_id else ""
    for bid in bids:
        action = default_block_action(bid, defaults)
        if action:
            continue
        errors.append(
            f"{prefix}{bid}: {REUSE_ACCEPT_MISS} — no BLOCK_DEFAULT_ACTIONS "
            f"entry ({PRODUCT_UNKNOWN_ACTION_NONE_HALT})"
        )
    if re.search(r"execute\s*\([^)]*action\s*=\s*None", text or ""):
        errors.append(
            f"{prefix}execute() passes action=None "
            f"({PRODUCT_UNKNOWN_ACTION_NONE_HALT})"
        )
    return errors


def reuse_accept_workspace_errors(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Scan REUSE handlers. Empty = WRITER may continue to TESTER."""
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    base = persist_workspace_root(root)
    errors: List[str] = []
    for item in inventory or ():
        if getattr(item, "is_gap", False):
            continue
        if not getattr(item, "is_reuse", False) and not getattr(
            item, "handler_source", ""
        ):
            if not getattr(item, "verified_present", None):
                continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if not cid:
            continue
        path = base / persist_handler_rel(cid)
        if not path.is_file():
            errors.append(
                f"{cid}: {REUSE_ACCEPT_MISS} — missing {persist_handler_rel(cid)}"
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{cid}: handler unreadable: {exc}")
            continue
        bids = [
            str(b)
            for b in (
                getattr(item, "verified_present", None)
                or getattr(item, "block_ids", None)
                or ()
            )
            if str(b).strip()
        ]
        errors.extend(
            reuse_accept_handler_errors(text, bids, capability_id=cid)
        )
    return errors


def assert_reuse_schema_accept(root: Path, compiled_or_inventory: Any) -> None:
    """Fail closed before TESTER / PRODUCT accept-payload."""
    errors = reuse_accept_workspace_errors(root, compiled_or_inventory)
    if errors:
        raise ReuseAcceptHalt(
            WRITER_REUSE_ACCEPT_HALT + ": " + "; ".join(errors[:8])
        )


def reuse_accept_rules_text(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """BUILD cut: schema-sample POSTs must not yield Unknown action."""
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    roster = named or list(LIVE_VETCARE_REUSE_ACCEPT_CAPS)
    return "\n".join(
        [
            "PRODUCT / writer_behaviour schema-sample accept (REUSE keep-path):",
            "The harness POSTs /v1/{capability_id} with a payload built from",
            "that capability's own FIELDS + CONSTRAINTS, then runs bound",
            "blocks. A keep-path handler that calls execute() with no",
            f"action= keyword (or action=None) fails as {PRODUCT_UNKNOWN_ACTION_HALT!r}",
            f"/ {PRODUCT_UNKNOWN_ACTION_NONE_HALT!r}. Workflow children without",
            f"step.action fail as {PRODUCT_EVENT_BUS_STEP_0_HALT}",
            f"({PRODUCT_EVENT_BUS_STEP_CLASS}).",
            "",
            "factory-grounded REUSE emit MUST populate BLOCK_DEFAULT_ACTIONS",
            "from vendored block.json (action default / options[0]) or the",
            "factory-known Store map. Pass action= as a keyword — never",
            "inside the payload dict. Prefer",
            "action=BLOCK_DEFAULT_ACTIONS.get(block_id).",
            "",
            "Photographed VetCare Hub REUSE roster (sess_78483eaf7acd4219):",
            *[f"- {cid}" for cid in roster],
            "Those ids are keep-path handlers, not per-cap micro-shots.",
            f"A miss is {REUSE_ACCEPT_MISS}: HALT before TESTER, do not burn",
            "three PRODUCT reworks on Unknown action.",
        ]
    )


def reuse_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    return (
        "- every REUSE keep-path handler accepts a schema-sample POST "
        f"without {PRODUCT_UNKNOWN_ACTION_HALT} / "
        f"{PRODUCT_UNKNOWN_ACTION_NONE_HALT} "
        f"({GATE_SCOPES['PRODUCT']})  "
        f"[check:{REUSE_ACCEPT_CHECK}]"
    )


def reuse_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live keep-path inventions PRODUCT then refuses."""
    return "\n".join(
        [
            f"- execute(block_id, payload) or action=None "
            f"({PRODUCT_UNKNOWN_ACTION_NONE_HALT})",
            "- empty BLOCK_DEFAULT_ACTIONS on a REUSE handler that binds Store blocks",
            "- burying action inside the payload dict",
            f"- reaching TESTER PRODUCT with {PRODUCT_UNKNOWN_ACTION_HALT} "
            f"or {PRODUCT_EVENT_BUS_STEP_0_HALT} after keep-path emit",
        ]
    )


def reuse_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        "REUSE keep-path handlers must accept a schema-sample POST. "
        "Populate BLOCK_DEFAULT_ACTIONS from block.json / the factory Store "
        "map and pass action= as a keyword "
        "(action=BLOCK_DEFAULT_ACTIONS.get(block_id)). "
        f"execute() with action=None is {PRODUCT_UNKNOWN_ACTION_NONE_HALT!r}. "
        f"Workflow step_0 without step.action is {PRODUCT_EVENT_BUS_STEP_0_HALT}. "
        f"That miss is {REUSE_ACCEPT_MISS}: HALT before TESTER. "
        f"Photographed roster: {', '.join(LIVE_VETCARE_REUSE_ACCEPT_CAPS)}."
    )


def reuse_accept_needles() -> Sequence[str]:
    """Needles lint requires on every compiled brief."""
    return (
        PRODUCT_UNKNOWN_ACTION_HALT,
        PRODUCT_UNKNOWN_ACTION_NONE_HALT,
        "BLOCK_DEFAULT_ACTIONS",
        "action= as a keyword",
        REUSE_ACCEPT_MISS,
        f"[check:{REUSE_ACCEPT_CHECK}]",
        LIVE_VETCARE_REUSE_ACCEPT_CAPS[0],
    )
