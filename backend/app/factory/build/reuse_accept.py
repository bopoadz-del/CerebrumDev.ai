"""REUSE keep-path schema-sample accept contract (C-BRIEF).

Photographed Floor after #346 (tip 5a530b3, sess_bb870f4fb29042f2,
VetCare Hub, all-REUSE): ModuleNotFoundError did not recur. Export
refuse PASS; pilot_zip=no. WRITER stopped at [check:reuse_accept]
(fail-closed before TESTER):

    - prescription_management: formula_executor: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)
    - billing_and_invoicing: formula_executor: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

#346 harvested STORE_BLOCK_DEFAULT_ACTIONS for the #345 roster
(database / validation / event_bus / workflow / analytics / team) but
keep-path emit left ``action=None`` for ``formula_executor`` — that
Store id is bound on the photographed REUSE caps and is missing from
the factory-known default map. Harvest also skipped the factory
vendor_blocks_mirror when workspace vendor/block.json had no action
input.

Live sess_07dff0eaf8f64186 (tip da7cd2b / #348): Unknown action /
formula_executor / ModuleNotFoundError did not recur. TESTER PRODUCT
then refused appointment_scheduling:

    workflow: RuntimeError: 'result'
    schema sample refused; accept-payload persisted nothing

Store workflow / kit shim reads input['result'] or out['result']. The
schema-sample POST does not include that key. This is prepare + emit +
CLONER rewrite — not a per-cap handle() micro-shot.

Live sess_8259e197749b4441 (tip 467c83e / #350): the ``result`` key miss
did not recur. WRITER stopped at [check:reuse_accept]:

    patient_records_management: vector_search: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

Registry-verified Cerebrum-Blocks ``vector_search/block.json`` has no
``inputs[].name == action`` (Store runtime uses ``params.operation``
default ``search``). Factory vendor_blocks_mirror also lacked that
harvest, and the documented Store map omitted the id. Same class as
#348 ``formula_executor``. Do not claim pilot_zip.

Live sess_c63cc1a274994b33 (VetClinic Hub ALL-REUSE, tip 467c83e / #350):
RuntimeError: 'result' did not recur. TESTER PRODUCT then refused after
rework×3:

    appointment_scheduling rejected a payload built from its own schema:
    queue: SyntaxError: cannot assign to function call  (queue.py ~line 189)
    workflow: step_0 (event_bus): error
    billing_and_invoicing: formula_executor same SyntaxError (~line 242)
    schema sample refused; accept-payload persisted nothing

#350 rewrote any identifier ``['result']`` to ``.get("result", obj)``,
including assignment targets (``something(x) = ...``). That is emit /
CLONER, not a per-cap handle() micro-shot. Do not claim pilot_zip.

Live sess_aed3e6e288414fcf (VetClinic Hub ALL-REUSE, tip 0963a6b / #352):
#351 vector_search Unknown action CLEARED. #352 SyntaxError CLEARED
(did not recur). TESTER PRODUCT then refused after rework×3:

    appointment_scheduling rejected a payload built from its own schema:
    workflow: RuntimeError: 'result'

#352 fail-closed kept the *whole* original module when any rewrite
missed a Store-ctx target (``for name['result'] in …``). Store workflow
reads stayed as ``['result']`` and wrapped KeyError as RuntimeError.
Fail-closed must skip that write and still rewrite reads. prepare /
keep-path emit must still attach input['result']. Do not claim pilot_zip.

Live sess_e8e4ab66e6dd4765 (tip 3b9261b, estate-operations / Private
Estate Steward Platform): WRITER stopped at [check:reuse_accept]:

    maintenance_and_work_order_management: capture: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)
    security_and_access_logging: capture: reuse/accept miss —

Registry / live Store vendor ``capture/block.json`` has OCR-config
inputs only (no ``inputs[].name == action`` / ``operation``). Factory
vendor ``block.py`` is an adapter with no action dispatch. Harvest
from block.json alone misses. InsureDistribute Store-green zip
(sess_d10dfc28) emitted ``BLOCK_DEFAULT_ACTIONS = {'capture':
'extract'}``. Factory-known map fallback is ``capture`` → ``extract``.
Same class as #348 ``formula_executor`` / #351 ``vector_search``.
Do not claim pilot_zip.

Live sess_5782f2264e0e4ff4 Continue run3 (tip 4120a07 / #404 CLONER OK,
#403 property_onboarding / spec_analyzer CLEARED): WRITER stopped at
[check:reuse_accept]:

    estate_registry: storage: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

Outcome FAILED_ROLE_ERROR. TESTER not reached. Steward
``estate_registry`` binds estate_registry + database + storage +
validation. #403 harvested spec_analyzer / recommendation_template /
readiness_engine; ``storage`` (and the other Steward kit adapters
without an action input) were still missing from the factory-known
map. Same class — not a per-cap handle() micro-shot.

``factory budget ramp`` is independent of this check. Ramp fires
during the in-flight C-BRIEF CLI wait (``_maybe_cli_phase_ramp``)
when leftover phase-box time is inside
``CLI_PHASE_RAMP_HEADROOM_S``. reuse_accept runs only after the CLI
returns and keep-path emit lands handlers. A miss here cannot
suppress a ramp that should already have logged; a Continue that
never approached the 1500s phase box also logs no ramp. Do not
claim pilot_zip.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.persist_accept import persist_handler_rel, persist_workspace_root
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.workspace import relpath_exists, relpath_read_text
from app.factory.build.reuse_lookup import (
    load_local_block_json,
    local_block_json_candidates,
)
from app.factory.build.workflow_accept import (
    EVENT_BUS_STEP_ACTION,
    PRODUCT_EVENT_BUS_STEP_0_HALT,
    PRODUCT_EVENT_BUS_STEP_CLASS,
    PRODUCT_WORKFLOW_RESULT_HALT,
)

PRODUCT_UNKNOWN_ACTION_HALT = "Unknown action"
PRODUCT_UNKNOWN_ACTION_NONE_HALT = "Unknown action: None"
#: Live sess_c63cc1a274994b33 after #350: CLONER result-key rewrite
#: turned ``name['result'] =`` into ``name.get("result", name) =``.
PRODUCT_ASSIGN_TO_CALL_HALT = "SyntaxError: cannot assign to function call"
#: Live sess_aed3e6e288414fcf after #352: whole-module keep-original
#: dropped workflow ``['result']`` read rewrites. TESTER exact class:
PRODUCT_SCHEMA_SAMPLE_REJECT = (
    "appointment_scheduling rejected a payload built from its own schema"
)
FAIL_CLOSED_MUST_REWRITE_READS = (
    "fail-closed keep original must still rewrite reads"
)
REUSE_ACCEPT_CHECK = "reuse_accept"
WRITER_REUSE_ACCEPT_HALT = (
    "WRITER [check:reuse_accept] failed — REUSE handler cannot accept "
    "its own schema sample (Unknown action / action=None)"
)
REUSE_ACCEPT_MISS = "reuse/accept miss"

#: Photographed VetCare Hub REUSE roster after #346 (sess_bb870f4fb29042f2)
#: plus sess_8259e197749b4441 ``vector_search`` on patient_records.
LIVE_VETCARE_REUSE_ACCEPT_CAPS = (
    "patient_records_management",
    "appointment_scheduling",
    "prescription_management",
    "billing_and_invoicing",
    "client_communication_portal",
)

LIVE_VETCARE_REUSE_ACCEPT_BLOCKS: Dict[str, List[str]] = {
    "patient_records_management": ["database", "validation", "vector_search"],
    "appointment_scheduling": ["event_bus", "workflow"],
    "prescription_management": ["validation", "formula_executor"],
    "billing_and_invoicing": ["analytics", "formula_executor"],
    "client_communication_portal": ["team"],
}

#: Photographed Steward Continue run3 (sess_5782f2264e0e4ff4, tip 4120a07).
LIVE_STEWARD_ESTATE_REGISTRY_CAP = "estate_registry"
LIVE_STEWARD_ESTATE_REGISTRY_BLOCKS: List[str] = [
    "estate_registry",
    "database",
    "storage",
    "validation",
]

#: Factory-known Store defaults already documented in this repo
#: (LIVE_CONTRACTS, workflow_accept, writer_behaviour / contract probes).
#: Harvest from vendored block.json / source wins when present.
#: ``formula_executor`` is the sess_bb870f4fb29042f2 miss: dual-registered
#: budget block; Store runtime alias is ``formula_executor_v2``.
#: ``vector_search`` is the sess_8259e197749b4441 miss: registry
#: block.json has no action input; Store ``process()`` defaults
#: ``params.operation`` to ``search``.
#: ``capture`` is the sess_e8e4ab66e6dd4765 miss: registry / live Store
#: vendor ``block.json`` has OCR-config inputs only (no ``action`` /
#: ``operation``). Factory vendor ``block.py`` is an adapter with no
#: action dispatch. InsureDistribute Store-green zip (sess_d10dfc28)
#: emitted ``BLOCK_DEFAULT_ACTIONS = {'capture': 'extract'}``. Harvest
#: aliases ``capture_v2``.
#: ``spec_analyzer`` / ``recommendation_template`` / ``readiness_engine``
#: are the sess_5782f2264e0e4ff4 miss on Steward ``property_onboarding``
#: (Unknown action: None). reuse_accept failed independently of the
#: ~1490s phase wall — the CLI had already returned at ~1488s.
#: ``storage`` is the sess_5782f2264e0e4ff4 run3 miss on Steward
#: ``estate_registry`` (tip 4120a07). RESOURCE_OBLIGATIONS.ensure is
#: ``store``; retrieve/exists/delete are follow-on actions. Factory
#: vendor ``storage/block.json`` has no action input (adapter run()
#: only). Sibling Steward adapters without an action input get the
#: same map so the next Continue cannot whack-a-mole:
#: estate_registry / estate_maintenance / evidence_verifier /
#: portfolio_rollup / knowledge.
STORE_BLOCK_DEFAULT_ACTIONS: Dict[str, str] = {
    "analytics": "track_event",
    "audit": "log",
    "capture": "extract",
    "capture_v2": "extract",
    "dashboard": "render",
    "database": "query",
    "document_engine": "parse",
    "event_bus": EVENT_BUS_STEP_ACTION,
    "formula_executor": "execute",
    "formula_executor_v2": "execute",
    "notification": "send",
    "queue": "enqueue",
    "team": "create_team",
    "validation": "validate",
    "vector_search": "search",
    "workflow": "run",
    #: sess_5782f2264e0e4ff4: Steward property_onboarding binds these three.
    #: Factory vendor mirrors have no inputs[].name == action (adapter
    #: run() only). Same class as formula_executor / vector_search / capture.
    "spec_analyzer": "analyze",
    "recommendation_template": "apply_template",
    "readiness_engine": "score",
    #: sess_5782f2264e0e4ff4 run3: Steward estate_registry binds storage.
    #: Factory vendor mirrors have no inputs[].name == action (adapter
    #: run() only). Same class as formula_executor / vector_search /
    #: capture / spec_analyzer.
    "storage": "store",
    "estate_registry": "register",
    "estate_maintenance": "plan_work",
    "evidence_verifier": "verify",
    "portfolio_rollup": "aggregate",
    "knowledge": "search",
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
#: Store vector_search (and similar) dispatch on operation, not action.
_OPERATION_DEFAULT = re.compile(
    r"""(?:params|kwargs)\.get\(\s*['\"]operation['\"]\s*,\s*['\"]([A-Za-z_][\w]*)['\"]"""
)
_ACTION_INPUT_NAMES = frozenset({"action", "operation"})


class ReuseAcceptHalt(ValueError):
    """WRITER must not claim done: REUSE schema-sample would Unknown action."""


def _harvest_candidate_ids(block_id: str) -> List[str]:
    """Exact id plus the Store ``_v2`` / kit-shelf alias (formula_executor)."""
    bid = str(block_id or "").strip()
    if not bid:
        return []
    ids = [bid]
    if bid.endswith("_v2") and len(bid) > 3:
        ids.append(bid[:-3])
    else:
        ids.append(f"{bid}_v2")
    seen: Dict[str, None] = {}
    for item in ids:
        if item:
            seen.setdefault(item, None)
    return list(seen)


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
    for cand_id in _harvest_candidate_ids(bid):
        if isinstance(default_actions, Mapping):
            alias = default_actions.get(cand_id)
            if isinstance(alias, str) and alias.strip():
                return alias.strip()
        mapped = STORE_BLOCK_DEFAULT_ACTIONS.get(cand_id)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    return None


def default_action_from_block_json(meta: Any) -> Optional[str]:
    """``inputs[].name == action`` (or ``operation``) default, else first option."""
    if not isinstance(meta, Mapping):
        return None
    found_operation: Optional[str] = None
    for item in meta.get("inputs") or ():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        if name not in _ACTION_INPUT_NAMES:
            continue
        harvested: Optional[str] = None
        raw = item.get("default")
        if isinstance(raw, str) and raw.strip():
            harvested = raw.strip()
        else:
            options = item.get("options") or ()
            if isinstance(options, (list, tuple)):
                for opt in options:
                    if isinstance(opt, str) and opt.strip():
                        harvested = opt.strip()
                        break
        if not harvested:
            continue
        if name == "action":
            return harvested
        if found_operation is None:
            found_operation = harvested
    return found_operation


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
    match = _OPERATION_DEFAULT.search(blob)
    if match:
        return (match.group(1) or "").strip() or None
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


def _harvest_from_factory_vendor(block_id: str) -> Optional[str]:
    """Prefer factory / Blocks-root block.json, then sibling source."""
    for cand in _harvest_candidate_ids(block_id):
        harvested = default_action_from_block_json(load_local_block_json(cand))
        if harvested:
            return harvested
        for path in local_block_json_candidates(cand):
            harvested = default_action_from_source(_read_text(path.with_name("block.py")))
            if harvested:
                return harvested
    return None


def harvest_block_default_action(
    block_id: str,
    *roots: Any,
    workspace: Any = None,
) -> Optional[str]:
    """Vendored block.json / source, then the factory-known Store map."""
    bid = str(block_id or "").strip()
    if not bid:
        return None
    candidates = _harvest_candidate_ids(bid)
    # Path.has exists(), but exists(rel) is the RoleWorkspace protocol.
    # getattr(..., "exists") treated Path as that duck-type and crashed
    # (CEREBRUMDEV-BACKEND-W). Protocol detection requires exists(rel) /
    # read_text(rel); Path-like bound methods fall through to _workspace_roots.
    for cand in candidates:
        meta_rel = Path("vendor") / "blocks" / cand / "block.json"
        if relpath_exists(workspace, meta_rel):
            try:
                meta = json.loads(relpath_read_text(workspace, meta_rel))
            except (ValueError, TypeError):
                meta = None
            harvested = default_action_from_block_json(meta)
            if harvested:
                return harvested
        for rel in (
            Path("vendor") / "blocks" / cand / "block.py",
            Path("vendor") / "cerebrum" / "blocks" / f"{cand}.py",
        ):
            if relpath_exists(workspace, rel):
                harvested = default_action_from_source(
                    relpath_read_text(workspace, rel)
                )
                if harvested:
                    return harvested
    for root in _workspace_roots(*roots, workspace=workspace):
        for cand in candidates:
            meta = _load_json(root / "vendor" / "blocks" / cand / "block.json")
            harvested = default_action_from_block_json(meta)
            if harvested:
                return harvested
            for rel in (
                Path("vendor") / "blocks" / cand / "block.py",
                Path("vendor") / "cerebrum" / "blocks" / f"{cand}.py",
            ):
                harvested = default_action_from_source(_read_text(root / rel))
                if harvested:
                    return harvested
    harvested = _harvest_from_factory_vendor(bid)
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
            f"({PRODUCT_EVENT_BUS_STEP_CLASS}). Store workflow / kit shim",
            "reads input['result'] / out['result']; a schema-sample POST",
            f"that omits it fails as {PRODUCT_WORKFLOW_RESULT_HALT}.",
            "prepare_block_input and keep-path emit MUST attach result from",
            "the first prepared step so accept-payload can persist.",
            "CLONER emit_result_key_access rewrites reads of name['result']",
            "only — assignment targets must stay subscripts. Rewriting",
            f"name['result'] = into a .get() call fails as {PRODUCT_ASSIGN_TO_CALL_HALT}",
            "(live queue.py ~189 / formula_executor ~242).",
            f"{FAIL_CLOSED_MUST_REWRITE_READS} — a whole-module keep of the",
            "original Store workflow.py leaves envelope['result'] /",
            f"input['result'] as KeyError → {PRODUCT_WORKFLOW_RESULT_HALT}",
            f"({PRODUCT_SCHEMA_SAMPLE_REJECT}).",
            "",
            "factory-grounded REUSE emit MUST populate BLOCK_DEFAULT_ACTIONS",
            "from vendored block.json (workspace vendor/, then factory",
            "vendor_blocks_mirror / CEREBRUM_BLOCKS_ROOT action default or",
            "options[0]) or the factory-known Store map. formula_executor",
            "(and formula_executor_v2) must harvest a keyword action.",
            "vector_search must harvest a keyword action (Store operation",
            "default search) even when registry block.json has no action",
            "input. capture must harvest a keyword action (Store-green",
            "extract, sess_d10dfc28) even when registry / live vendor",
            "block.json has no action input. Pass action= as a keyword —",
            "never inside the payload dict.",
            "Prefer action=BLOCK_DEFAULT_ACTIONS.get(block_id).",
            "",
            "Photographed VetCare Hub REUSE roster (sess_bb870f4fb29042f2 /",
            "sess_8259e197749b4441):",
            *[f"- {cid}" for cid in roster],
            "Those ids are keep-path handlers, not per-cap micro-shots.",
            "prescription_management / billing_and_invoicing bind",
            "formula_executor — a missing default is reuse/accept miss.",
            "patient_records_management binds vector_search — a missing",
            "default is the sess_8259e197749b4441 reuse/accept miss.",
            "estate-operations maintenance_and_work_order_management /",
            "security_and_access_logging bind capture — a missing default",
            "is the sess_e8e4ab66e6dd4765 reuse/accept miss.",
            "Steward property_onboarding binds spec_analyzer /",
            "recommendation_template / readiness_engine — a missing",
            "default is the sess_5782f2264e0e4ff4 reuse/accept miss",
            "(independent of the ~1490s phase wall).",
            "Steward estate_registry binds storage — a missing default",
            "is the sess_5782f2264e0e4ff4 run3 reuse/accept miss",
            "(tip 4120a07; independent of factory budget ramp).",
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
            "- omitting workflow input['result'] so PRODUCT fails as "
            f"{PRODUCT_WORKFLOW_RESULT_HALT} (accept-payload persisted nothing)",
            "- rewriting name['result'] = into name.get(...) = so PRODUCT "
            f"fails as {PRODUCT_ASSIGN_TO_CALL_HALT} (queue / "
            "formula_executor Store shims assign that key)",
            "- fail-closed keeping the whole original module so PRODUCT "
            f"fails as {PRODUCT_WORKFLOW_RESULT_HALT} "
            f"({FAIL_CLOSED_MUST_REWRITE_READS})",
        ]
    )


def reuse_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        "REUSE keep-path handlers must accept a schema-sample POST. "
        "Populate BLOCK_DEFAULT_ACTIONS from block.json / the factory Store "
        "map (including formula_executor, vector_search, capture, "
        "spec_analyzer, storage, and estate_registry) and pass action= "
        "as a keyword (action=BLOCK_DEFAULT_ACTIONS.get(block_id)). "
        f"execute() with action=None is {PRODUCT_UNKNOWN_ACTION_NONE_HALT!r}. "
        f"Workflow step_0 without step.action is {PRODUCT_EVENT_BUS_STEP_0_HALT}. "
        "Store workflow reads input['result'] — a schema-sample POST that "
        f"omits it fails as {PRODUCT_WORKFLOW_RESULT_HALT!r}. "
        "CLONER must not rewrite assignment targets: name['result'] = "
        f"becoming a .get() call fails as {PRODUCT_ASSIGN_TO_CALL_HALT!r} "
        "(queue / formula_executor). "
        f"{FAIL_CLOSED_MUST_REWRITE_READS} or TESTER refuses "
        f"{PRODUCT_SCHEMA_SAMPLE_REJECT}: {PRODUCT_WORKFLOW_RESULT_HALT!r}. "
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
        "formula_executor",
        "vector_search",
        "capture",
        "Steward estate_registry binds storage",
        "estate_registry",
        PRODUCT_WORKFLOW_RESULT_HALT,
        "input['result']",
        PRODUCT_ASSIGN_TO_CALL_HALT,
        "name['result'] =",
        FAIL_CLOSED_MUST_REWRITE_READS,
        PRODUCT_SCHEMA_SAMPLE_REJECT,
    )
