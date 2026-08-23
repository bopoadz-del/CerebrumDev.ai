"""S12 domain acceptance emitters for RoleRunner products.

Ten named business outcomes must be PERFORMED through the shipped kernel
(``cerebrum_product_kernel`` / ``execute_action``). Unconditional HTTP
``ok: true`` is F1. Outcomes are capability/contract driven so they apply
to any RoleRunner product, not one vertical.

LotDesk-class fixtures fail this gate (always-200, missing update/delete,
hollow queue). The fixture is inspected, never patched.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.factory.build.data_lifecycle import (
    IDEMPOTENCY_TABLE,
    WORK_QUEUE_TABLE,
    first_entity_sample,
)
from app.factory.build.lotdesk_gate import inspect_path, resolve_lotdesk_fixture

WRITE_PERMISSION = "product.write"
READ_PERMISSION = "product.read"
PROCESS_PERMISSION = "product.process"

OUTCOME_CREATE_PERSISTS = "create_persists"
OUTCOME_READ_RETURNS_PERSISTED = "read_returns_persisted"
OUTCOME_UPDATE_PERSISTS = "update_persists"
OUTCOME_DELETE_PERSISTS = "delete_persists"
OUTCOME_LIST_ONLY_PERSISTED = "list_only_persisted"
OUTCOME_QUEUE_ITEM_PROCESSED = "queue_item_processed"
OUTCOME_REFUSED_ACTION_ERRORS = "refused_action_errors"
OUTCOME_IDEMPOTENT_DUPLICATE_SAFE = "idempotent_duplicate_safe"
OUTCOME_UNAUTHORIZED_REJECTED = "unauthorized_rejected"
OUTCOME_MISSING_FIELD_REJECTED = "missing_field_rejected"

OUTCOMES: tuple[str, ...] = (
    OUTCOME_CREATE_PERSISTS,
    OUTCOME_READ_RETURNS_PERSISTED,
    OUTCOME_UPDATE_PERSISTS,
    OUTCOME_DELETE_PERSISTS,
    OUTCOME_LIST_ONLY_PERSISTED,
    OUTCOME_QUEUE_ITEM_PROCESSED,
    OUTCOME_REFUSED_ACTION_ERRORS,
    OUTCOME_IDEMPOTENT_DUPLICATE_SAFE,
    OUTCOME_UNAUTHORIZED_REJECTED,
    OUTCOME_MISSING_FIELD_REJECTED,
)

OUTCOME_CATALOG: tuple[Dict[str, str], ...] = (
    {
        "id": OUTCOME_CREATE_PERSISTS,
        "via": "execute_action product.create then store.get",
        "closes": "F6",
        "requires": "row persists and reads back",
    },
    {
        "id": OUTCOME_READ_RETURNS_PERSISTED,
        "via": "execute_action product.read",
        "closes": "F6",
        "requires": "read returns the persisted fields",
    },
    {
        "id": OUTCOME_UPDATE_PERSISTS,
        "via": "execute_action product.update then store.get",
        "closes": "F6",
        "requires": "changed fields persist and read back",
    },
    {
        "id": OUTCOME_DELETE_PERSISTS,
        "via": "execute_action product.delete then store.get",
        "closes": "F6",
        "requires": "deleted id is gone",
    },
    {
        "id": OUTCOME_LIST_ONLY_PERSISTED,
        "via": "execute_action product.list",
        "closes": "F6",
        "requires": "list/search ids equal store.list_all ids",
    },
    {
        "id": OUTCOME_QUEUE_ITEM_PROCESSED,
        "via": "execute_action product.enqueue then product.process",
        "closes": "F5",
        "requires": "pending item is processed, not a no-op",
    },
    {
        "id": OUTCOME_REFUSED_ACTION_ERRORS,
        "via": "execute_action product.refuse / invalid contract",
        "closes": "F1,F12",
        "requires": "status is not success and envelope is not ok:true",
    },
    {
        "id": OUTCOME_IDEMPOTENT_DUPLICATE_SAFE,
        "via": "execute_action product.create with idempotency_key",
        "closes": "F6",
        "requires": "duplicate key returns the same row",
    },
    {
        "id": OUTCOME_UNAUTHORIZED_REJECTED,
        "via": "execute_action with empty permissions",
        "closes": "F15",
        "requires": "permission_denied, not ok:true",
    },
    {
        "id": OUTCOME_MISSING_FIELD_REJECTED,
        "via": "execute_action product.create missing required field",
        "closes": "F4",
        "requires": "validation_error, not ok:true",
    },
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def compact_specs(specs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Capability contract used by the generated domain ops (no coder metadata)."""
    out: Dict[str, Dict[str, Any]] = {}
    for cap_id, spec in specs.items():
        fields = []
        for field in spec.get("fields") or []:
            item = {
                "name": field["name"],
                "type": field.get("type") or "str",
                "required": bool(field.get("required")),
            }
            if field.get("allowed_values"):
                item["allowed_values"] = list(field["allowed_values"])
            if field.get("min") is not None:
                item["min"] = field["min"]
            if field.get("max") is not None:
                item["max"] = field["max"]
            fields.append(item)
        out[cap_id] = {
            "entity": spec.get("entity") or cap_id.replace("-", "_"),
            "fields": fields,
        }
    return out


def first_capability_id(specs: Dict[str, Dict[str, Any]]) -> str:
    if not specs:
        return ""
    return sorted(specs)[0]


def render_work_queue() -> str:
    return f'''"""Persisted work queue processed through execute_action.

Enqueue writes a pending row. Process runs the capability through the
vendored kernel and records processed|failed plus the ActionResult.
A handler that returns ok without changing status is a hollow queue (F5).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.store import connect

TABLE = {WORK_QUEUE_TABLE!r}
IDEMPOTENCY = {IDEMPOTENCY_TABLE!r}
PENDING = "pending"
PROCESSED = "processed"
FAILED = "failed"


def enqueue(
    capability_id: str,
    payload: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    conn = connect()
    try:
        cur = conn.execute(
            f"INSERT INTO {{TABLE}} (capability_id, payload, status, result, "
            "idempotency_key) VALUES (?, ?, ?, ?, ?)",
            (
                capability_id,
                json.dumps(payload, sort_keys=True),
                PENDING,
                None,
                idempotency_key,
            ),
        )
        conn.commit()
        item_id = int(cur.lastrowid)
    finally:
        conn.close()
    item = get(item_id)
    if item is None:
        raise RuntimeError("work_queue enqueue did not persist")
    return item


def get(item_id: int) -> Dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {{TABLE}} WHERE id = ?", (item_id,)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def list_all() -> List[Dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(f"SELECT * FROM {{TABLE}} ORDER BY id").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def mark(item_id: int, status: str, result: Dict[str, Any] | None) -> Dict[str, Any] | None:
    conn = connect()
    try:
        cur = conn.execute(
            f"UPDATE {{TABLE}} SET status = ?, result = ? WHERE id = ?",
            (
                status,
                json.dumps(result, sort_keys=True) if result is not None else None,
                item_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    return get(item_id)


def recall(key: str) -> Dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {{IDEMPOTENCY}} WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def remember(key: str, entity: str, record_id: int) -> None:
    conn = connect()
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO {{IDEMPOTENCY}} (key, entity, record_id) "
            "VALUES (?, ?, ?)",
            (key, entity, record_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    raw_payload = item.get("payload")
    if isinstance(raw_payload, str):
        try:
            item["payload"] = json.loads(raw_payload)
        except json.JSONDecodeError:
            pass
    raw_result = item.get("result")
    if isinstance(raw_result, str) and raw_result:
        try:
            item["result"] = json.loads(raw_result)
        except json.JSONDecodeError:
            pass
    return item
'''


def render_domain_ops(specs: Dict[str, Dict[str, Any]]) -> str:
    compact = compact_specs(specs)
    cap_id = first_capability_id(specs)
    entity, sample = first_entity_sample(specs)
    return f'''"""S12 domain outcomes performed through execute_action.

CRUD, list, queue process, refusal, idempotency, unauthorized, and
missing-field rejection are kernel ActionSpecs. HTTP ok:true is not
acceptance. LLM-authored route bodies are forbidden on this path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import store, work_queue
from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.runtime import execute_action

SPECS: Dict[str, Dict[str, Any]] = {compact!r}
DEFAULT_CAPABILITY = {cap_id!r}
DEFAULT_ENTITY = {entity!r}
SAMPLE = {sample!r}

WRITE_PERMISSION = {WRITE_PERMISSION!r}
READ_PERMISSION = {READ_PERMISSION!r}
PROCESS_PERMISSION = {PROCESS_PERMISSION!r}

OUTCOME_CREATE_PERSISTS = {OUTCOME_CREATE_PERSISTS!r}
OUTCOME_READ_RETURNS_PERSISTED = {OUTCOME_READ_RETURNS_PERSISTED!r}
OUTCOME_UPDATE_PERSISTS = {OUTCOME_UPDATE_PERSISTS!r}
OUTCOME_DELETE_PERSISTS = {OUTCOME_DELETE_PERSISTS!r}
OUTCOME_LIST_ONLY_PERSISTED = {OUTCOME_LIST_ONLY_PERSISTED!r}
OUTCOME_QUEUE_ITEM_PROCESSED = {OUTCOME_QUEUE_ITEM_PROCESSED!r}
OUTCOME_REFUSED_ACTION_ERRORS = {OUTCOME_REFUSED_ACTION_ERRORS!r}
OUTCOME_IDEMPOTENT_DUPLICATE_SAFE = {OUTCOME_IDEMPOTENT_DUPLICATE_SAFE!r}
OUTCOME_UNAUTHORIZED_REJECTED = {OUTCOME_UNAUTHORIZED_REJECTED!r}
OUTCOME_MISSING_FIELD_REJECTED = {OUTCOME_MISSING_FIELD_REJECTED!r}

OUTCOMES = (
    OUTCOME_CREATE_PERSISTS,
    OUTCOME_READ_RETURNS_PERSISTED,
    OUTCOME_UPDATE_PERSISTS,
    OUTCOME_DELETE_PERSISTS,
    OUTCOME_LIST_ONLY_PERSISTED,
    OUTCOME_QUEUE_ITEM_PROCESSED,
    OUTCOME_REFUSED_ACTION_ERRORS,
    OUTCOME_IDEMPOTENT_DUPLICATE_SAFE,
    OUTCOME_UNAUTHORIZED_REJECTED,
    OUTCOME_MISSING_FIELD_REJECTED,
)

WRITE_OPS = frozenset({{"create", "update", "delete", "enqueue", "refuse"}})
READ_OPS = frozenset({{"read", "list"}})


def entity_of(capability_id: str) -> str:
    spec = SPECS.get(capability_id) or SPECS.get(DEFAULT_CAPABILITY) or {{}}
    return str(spec.get("entity") or capability_id.replace("-", "_"))


def fields_of(capability_id: str) -> List[Dict[str, Any]]:
    spec = SPECS.get(capability_id) or SPECS.get(DEFAULT_CAPABILITY) or {{}}
    return list(spec.get("fields") or [])


def sample_payload(capability_id: str) -> Dict[str, Any]:
    if capability_id == DEFAULT_CAPABILITY and SAMPLE:
        return dict(SAMPLE)
    payload: Dict[str, Any] = {{}}
    for field in fields_of(capability_id):
        name = field["name"]
        allowed = field.get("allowed_values") or []
        ftype = field.get("type") or "str"
        if allowed:
            payload[name] = allowed[0]
        elif ftype == "int":
            payload[name] = int(field["min"]) if field.get("min") is not None else 1
        elif ftype == "float":
            payload[name] = float(field["min"]) if field.get("min") is not None else 1.5
        elif ftype == "bool":
            payload[name] = True
        else:
            payload[name] = "s12-row"
    return payload


def required_field_names(capability_id: str) -> List[str]:
    return [f["name"] for f in fields_of(capability_id) if f.get("required")]


def enum_field(capability_id: str) -> tuple[str, List[Any]] | None:
    for field in fields_of(capability_id):
        allowed = field.get("allowed_values") or []
        if len(allowed) >= 1:
            return field["name"], list(allowed)
    return None


def record_fields(capability_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    names = {{f["name"] for f in fields_of(capability_id)}}
    return {{k: arguments[k] for k in names if k in arguments}}


def _json_type(ftype: str) -> str:
    return {{"str": "string", "int": "integer", "float": "number", "bool": "boolean"}}.get(
        ftype, "string"
    )


def input_schema_for(op: str, capability_id: str) -> Dict[str, Any]:
    properties: Dict[str, Any] = {{
        "capability_id": {{"type": "string"}},
        "idempotency_key": {{"type": "string"}},
        "id": {{"type": "integer"}},
        "q": {{"type": "string"}},
        "payload": {{"type": "object"}},
        "reason": {{"type": "string"}},
    }}
    required: List[str] = []
    if op in {{"create", "update", "read", "delete", "list", "enqueue"}}:
        required.append("capability_id")
    if op in {{"read", "update", "delete"}}:
        required.append("id")
    if op == "process":
        required = ["id"]
    if op == "refuse":
        required = ["reason"]
    if op in {{"create", "update"}}:
        for field in fields_of(capability_id):
            prop: Dict[str, Any] = {{"type": _json_type(field.get("type") or "str")}}
            if field.get("allowed_values"):
                prop["enum"] = list(field["allowed_values"])
            properties[field["name"]] = prop
            if op == "create" and field.get("required"):
                required.append(field["name"])
    return {{"type": "object", "properties": properties, "required": required}}


def authorized_context() -> ActionContext:
    return ActionContext(
        user_id="operator",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[WRITE_PERMISSION, READ_PERMISSION, PROCESS_PERMISSION],
        allowed_domains=["product"],
    )


def unauthorized_context() -> ActionContext:
    return ActionContext(
        user_id="stranger",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[],
        allowed_domains=["product"],
    )


def _permissions_for(op: str) -> List[str]:
    if op in WRITE_OPS:
        return [WRITE_PERMISSION]
    if op == "process":
        return [PROCESS_PERMISSION]
    return [READ_PERMISSION]


async def _handle_create(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    entity = entity_of(capability_id)
    key = arguments.get("idempotency_key")
    if key:
        hit = work_queue.recall(str(key))
        if hit:
            row = store.get(hit["entity"], int(hit["record_id"]))
            if row is not None:
                return ActionOutcome.success({{"stored": row, "replayed": True}})
    stored = store.save(entity, record_fields(capability_id, arguments))
    if key:
        work_queue.remember(str(key), entity, int(stored["id"]))
    return ActionOutcome.success({{"stored": stored, "replayed": False}})


async def _handle_read(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    row = store.get(entity_of(capability_id), int(arguments["id"]))
    if row is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    return ActionOutcome.success({{"stored": row}})


async def _handle_update(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    entity = entity_of(capability_id)
    current = store.get(entity, int(arguments["id"]))
    if current is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    merged = {{**current, **record_fields(capability_id, arguments)}}
    updated = store.update(entity, int(arguments["id"]), merged)
    if updated is None:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="update_failed",
            error_message="update did not persist",
        )
    return ActionOutcome.success({{"stored": updated}})


async def _handle_delete(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    removed = store.delete(entity_of(capability_id), int(arguments["id"]))
    if not removed:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    return ActionOutcome.success({{"deleted": True, "id": int(arguments["id"])}})


async def _handle_list(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    items = store.list_all(entity_of(capability_id))
    query = arguments.get("q")
    if query:
        needle = str(query)
        items = [item for item in items if needle in json_blob(item)]
    return ActionOutcome.success({{"items": items}})


async def _handle_enqueue(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    payload = arguments.get("payload")
    if not isinstance(payload, dict):
        payload = record_fields(capability_id, arguments) or sample_payload(capability_id)
    item = work_queue.enqueue(
        capability_id,
        payload,
        idempotency_key=arguments.get("idempotency_key"),
    )
    if item.get("status") != work_queue.PENDING:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="hollow_queue",
            error_message="enqueue did not persist a pending item",
        )
    return ActionOutcome.success({{"item": item}})


async def _handle_process(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    item = work_queue.get(int(arguments["id"]))
    if item is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="queue item not found",
        )
    if item.get("status") != work_queue.PENDING:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_pending",
            error_message="queue item is not pending",
            output={{"item": item}},
        )
    from app.kernel_bridge import product_context, spec_for

    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {{}}
    result = await execute_action(spec_for(item["capability_id"]), product_context(), payload)
    status = (
        work_queue.PROCESSED
        if result.status == ActionStatus.SUCCESS
        else work_queue.FAILED
    )
    marked = work_queue.mark(int(item["id"]), status, result.to_dict())
    if marked is None or marked.get("status") == work_queue.PENDING:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="hollow_queue",
            error_message="process did not change the persisted queue status",
        )
    return ActionOutcome.success({{"item": marked, "result": result.to_dict()}})


async def _handle_refuse(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    return ActionOutcome(
        status=ActionStatus.VALIDATION_ERROR,
        error_code="refused",
        error_message=str(arguments.get("reason") or "refused"),
    )


HANDLERS = {{
    "create": _handle_create,
    "read": _handle_read,
    "update": _handle_update,
    "delete": _handle_delete,
    "list": _handle_list,
    "enqueue": _handle_enqueue,
    "process": _handle_process,
    "refuse": _handle_refuse,
}}


def spec_for_op(op: str, capability_id: str) -> ActionSpec:
    if op not in HANDLERS:
        raise ValueError(f"unknown domain op: {{op}}")
    return ActionSpec(
        action_id=f"product.{{op}}",
        domain="product",
        name=op,
        description=f"S12 {{op}} for {{capability_id}}",
        input_schema=input_schema_for(op, capability_id),
        output_schema={{}},
        required_context=[],
        permissions=_permissions_for(op),
        read_only=op in READ_OPS,
        handler=HANDLERS[op],
    )


async def perform(
    op: str,
    capability_id: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    context: Optional[ActionContext] = None,
) -> Dict[str, Any]:
    payload = dict(arguments or {{}})
    if op != "process" and op != "refuse":
        payload["capability_id"] = capability_id or DEFAULT_CAPABILITY
    result = await execute_action(
        spec_for_op(op, capability_id or DEFAULT_CAPABILITY),
        context or authorized_context(),
        payload,
    )
    return result.to_dict()


def json_blob(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _ok_true(result: Dict[str, Any]) -> bool:
    return result.get("ok") is True or result.get("status") == "success"


def _record(status: str, **detail: Any) -> Dict[str, Any]:
    return {{"status": status, **detail}}


async def perform_all(capability_id: Optional[str] = None) -> Dict[str, Any]:
    """Perform every named outcome through execute_action. No HTTP ok:true."""
    cap = capability_id or DEFAULT_CAPABILITY
    entity = entity_of(cap)
    sample = sample_payload(cap)
    ctx = authorized_context()
    outcomes: Dict[str, Any] = {{}}

    created = await perform("create", cap, dict(sample), context=ctx)
    stored = (created.get("output") or {{}}).get("stored") or {{}}
    fetched = store.get(entity, stored["id"]) if stored.get("id") is not None else None
    if created.get("status") == "success" and fetched is not None:
        field_ok = all(fetched.get(k) == sample[k] for k in sample)
        outcomes[OUTCOME_CREATE_PERSISTS] = _record(
            "performed" if field_ok else "failed",
            id=stored.get("id"),
            fields_match=field_ok,
        )
    else:
        outcomes[OUTCOME_CREATE_PERSISTS] = _record(
            "failed", error=created.get("error_message") or created.get("status")
        )

    read = await perform("read", cap, {{"id": stored.get("id")}}, context=ctx)
    read_row = (read.get("output") or {{}}).get("stored") or {{}}
    if (
        read.get("status") == "success"
        and stored.get("id") is not None
        and read_row.get("id") == stored.get("id")
    ):
        outcomes[OUTCOME_READ_RETURNS_PERSISTED] = _record(
            "performed", id=read_row.get("id")
        )
    else:
        outcomes[OUTCOME_READ_RETURNS_PERSISTED] = _record(
            "failed", error=read.get("error_message") or read.get("status")
        )

    mutated = dict(sample)
    enum = enum_field(cap)
    if enum and len(enum[1]) > 1:
        mutated[enum[0]] = enum[1][1]
    elif "quantity" in mutated and isinstance(mutated["quantity"], int):
        mutated["quantity"] = int(mutated["quantity"]) + 1
    elif "reference" in mutated:
        mutated["reference"] = str(mutated["reference"]) + "-upd"
    else:
        first = next(iter(mutated), None)
        if first and isinstance(mutated[first], str):
            mutated[first] = str(mutated[first]) + "-upd"
    updated = await perform(
        "update", cap, {{**mutated, "id": stored.get("id")}}, context=ctx
    )
    after = store.get(entity, stored["id"]) if stored.get("id") is not None else None
    update_ok = (
        updated.get("status") == "success"
        and after is not None
        and all(after.get(k) == mutated[k] for k in mutated)
    )
    outcomes[OUTCOME_UPDATE_PERSISTS] = _record(
        "performed" if update_ok else "failed",
        error=None if update_ok else (updated.get("error_message") or "update did not persist"),
    )

    listed = await perform("list", cap, {{}}, context=ctx)
    listed_ids = {{
        item.get("id")
        for item in (listed.get("output") or {{}}).get("items") or []
    }}
    persisted_ids = {{row["id"] for row in store.list_all(entity)}}
    list_ok = (
        listed.get("status") == "success"
        and listed_ids == persisted_ids
        and stored.get("id") in listed_ids
    )
    outcomes[OUTCOME_LIST_ONLY_PERSISTED] = _record(
        "performed" if list_ok else "failed",
        listed=sorted(x for x in listed_ids if x is not None),
        persisted=sorted(persisted_ids),
    )

    enq = await perform("enqueue", cap, {{"payload": dict(sample)}}, context=ctx)
    item = (enq.get("output") or {{}}).get("item") or {{}}
    before_status = item.get("status")
    proc = await perform("process", cap, {{"id": item.get("id")}}, context=ctx)
    after_item = (proc.get("output") or {{}}).get("item") or work_queue.get(item["id"]) if item.get("id") else {{}}
    queue_ok = (
        enq.get("status") == "success"
        and before_status == work_queue.PENDING
        and proc.get("status") == "success"
        and after_item
        and after_item.get("status") in {{work_queue.PROCESSED, work_queue.FAILED}}
        and after_item.get("status") != before_status
        and after_item.get("result")
    )
    outcomes[OUTCOME_QUEUE_ITEM_PROCESSED] = _record(
        "performed" if queue_ok else "failed",
        before=before_status,
        after=(after_item or {{}}).get("status"),
        error=None if queue_ok else (proc.get("error_message") or "hollow queue"),
    )

    refused = await perform("refuse", cap, {{"reason": "s12-refused"}}, context=ctx)
    invalid = None
    if enum:
        bad = dict(sample)
        bad[enum[0]] = "__not_in_contract__"
        invalid = await perform("create", cap, bad, context=ctx)
    refuse_ok = (
        refused.get("status") != "success"
        and not _ok_true(refused)
        and refused.get("error_message")
        and (invalid is None or (invalid.get("status") != "success" and not _ok_true(invalid)))
    )
    outcomes[OUTCOME_REFUSED_ACTION_ERRORS] = _record(
        "performed" if refuse_ok else "failed",
        refused_status=refused.get("status"),
        invalid_status=(invalid or {{}}).get("status"),
    )

    key = "s12-idempotent"
    first = await perform(
        "create", cap, {{**sample, "idempotency_key": key}}, context=ctx
    )
    second = await perform(
        "create", cap, {{**sample, "idempotency_key": key}}, context=ctx
    )
    first_id = ((first.get("output") or {{}}).get("stored") or {{}}).get("id")
    second_id = ((second.get("output") or {{}}).get("stored") or {{}}).get("id")
    replayed = ((second.get("output") or {{}}).get("replayed")) is True
    idem_ok = (
        first.get("status") == "success"
        and second.get("status") == "success"
        and first_id is not None
        and first_id == second_id
        and replayed
    )
    outcomes[OUTCOME_IDEMPOTENT_DUPLICATE_SAFE] = _record(
        "performed" if idem_ok else "failed",
        first_id=first_id,
        second_id=second_id,
        replayed=replayed,
    )

    denied = await perform("create", cap, dict(sample), context=unauthorized_context())
    unauth_ok = (
        denied.get("status") == "permission_denied"
        and not _ok_true(denied)
        and denied.get("error_code") == "permission_denied"
    )
    outcomes[OUTCOME_UNAUTHORIZED_REJECTED] = _record(
        "performed" if unauth_ok else "failed",
        action_status=denied.get("status"),
        error_code=denied.get("error_code"),
    )

    required = required_field_names(cap)
    missing_name = required[0] if required else None
    if missing_name:
        incomplete = {{k: v for k, v in sample.items() if k != missing_name}}
        missing = await perform("create", cap, incomplete, context=ctx)
        missing_ok = (
            missing.get("status") == "validation_error"
            and not _ok_true(missing)
            and missing.get("error_code") == "invalid_input"
        )
        outcomes[OUTCOME_MISSING_FIELD_REJECTED] = _record(
            "performed" if missing_ok else "failed",
            field=missing_name,
            action_status=missing.get("status"),
            error=missing.get("error_message"),
        )
    else:
        outcomes[OUTCOME_MISSING_FIELD_REJECTED] = _record(
            "failed", error="capability contract declares no required field"
        )

    doomed = await perform("create", cap, dict(sample), context=ctx)
    doomed_id = ((doomed.get("output") or {{}}).get("stored") or {{}}).get("id")
    deleted = await perform("delete", cap, {{"id": doomed_id}}, context=ctx)
    gone = store.get(entity, doomed_id) if doomed_id is not None else "no-id"
    delete_ok = (
        doomed.get("status") == "success"
        and deleted.get("status") == "success"
        and gone is None
    )
    outcomes[OUTCOME_DELETE_PERSISTS] = _record(
        "performed" if delete_ok else "failed",
        id=doomed_id,
        remaining=gone,
    )

    failed = [name for name in OUTCOMES if outcomes.get(name, {{}}).get("status") != "performed"]
    return {{
        "ok": not failed,
        "capability_id": cap,
        "entity": entity,
        "outcomes": outcomes,
        "failed": failed,
        "performed": [name for name in OUTCOMES if name not in failed],
        "kernel": "execute_action",
    }}
'''


def render_product_tests(specs: Dict[str, Dict[str, Any]]) -> str:
    cap_id = first_capability_id(specs)
    return f'''"""S12 domain acceptance — performed through execute_action.

Factory code-phase names the ten outcomes. Pilot performs them against
the migrated store. HTTP ok:true is not acceptance.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain_ops import OUTCOMES, perform_all
from app.migrations import upgrade_head

EXPECTED = {list(OUTCOMES)!r}
CAPABILITY = {cap_id!r}


def test_ten_named_outcomes_are_the_contract():
    assert list(OUTCOMES) == EXPECTED
    assert len(OUTCOMES) == 10
    assert "create_persists" in OUTCOMES
    assert "queue_item_processed" in OUTCOMES
    assert "refused_action_errors" in OUTCOMES


@pytest.mark.pilot
def test_ten_business_outcomes_are_performed_through_the_kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    upgrade_head()
    result = asyncio.run(perform_all(CAPABILITY or None))
    assert result["kernel"] == "execute_action"
    assert result["ok"] is True, result
    assert result["failed"] == []
    assert result["performed"] == list(OUTCOMES)
    for name in OUTCOMES:
        assert result["outcomes"][name]["status"] == "performed", (name, result)
'''


def declaration(specs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": "domain_acceptance.v1",
        "stage": "S12",
        "outcomes": [dict(item) for item in OUTCOME_CATALOG],
        "kernel": "cerebrum_product_kernel.contract.runtime.execute_action",
        "llm_route_authorship": "forbidden; _coder_route_body stays None",
        "http_ok_true_is": "F1",
        "capability_id": first_capability_id(specs),
        "permissions": [WRITE_PERMISSION, READ_PERMISSION, PROCESS_PERMISSION],
        "queue": {
            "table": WORK_QUEUE_TABLE,
            "idempotency_table": IDEMPOTENCY_TABLE,
            "hollow_is": "F5",
        },
        "lotdesk": "fixture only; not patched",
    }


def render_declaration(specs: Dict[str, Dict[str, Any]]) -> str:
    return json.dumps(declaration(specs), indent=2, sort_keys=True) + "\n"


def emit_writer_artifacts(workspace: Any, specs: Dict[str, Dict[str, Any]]) -> None:
    """Write kernel domain ops, work queue, and the outcome contract."""
    workspace.write_text(Path("app") / "work_queue.py", render_work_queue())
    workspace.write_text(Path("app") / "domain_ops.py", render_domain_ops(specs))
    workspace.write_text(
        Path("docs") / "domain_acceptance.json", render_declaration(specs)
    )


def _normalised_sources(path: Path) -> Dict[str, str]:
    findings_as_files: Dict[str, str] = {}
    target = Path(path)
    if target.is_file() and target.suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(target) as zf:
            for name in zf.namelist():
                if name.endswith(".py"):
                    findings_as_files[name.replace("\\", "/")] = zf.read(name).decode(
                        "utf-8", errors="replace"
                    )
        return findings_as_files
    for item in target.rglob("*.py"):
        rel = str(item.relative_to(target)).replace("\\", "/")
        findings_as_files[rel] = item.read_text(encoding="utf-8", errors="replace")
    return findings_as_files


def _basename_map(files: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, text in files.items():
        parts = name.replace("\\", "/").split("/")
        if "app" in parts:
            idx = parts.index("app")
            out["/".join(parts[idx:])] = text
        else:
            out[name.replace("\\", "/")] = text
    return out


def inspect_lotdesk_domain(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk cannot perform the ten outcomes on the kernel path."""
    path = resolve_lotdesk_fixture(explicit)
    files = _basename_map(_normalised_sources(path))
    store_src = files.get("app/store.py", "")
    routes_src = files.get("app/routes.py", "")
    main_src = files.get("app/main.py", "")
    queue_src = files.get("app/work_queue.py", "")
    domain_src = files.get("app/domain_ops.py", "")
    joined = "\n".join(files.values())
    has_kernel = "def execute_action" in joined or "execute_action" in domain_src
    has_update = "def update(" in store_src
    has_delete = "def delete(" in store_src
    has_put = "@router.put" in routes_src
    has_http_delete = "@router.delete" in routes_src
    has_process = "def mark(" in queue_src and "PROCESSED" in queue_src
    always_200 = 'return {"status": "ok"}' in main_src or "return {'status': 'ok'}" in main_src
    vendor_queue = any(
        name.endswith("vendor/blocks/queue/block.py")
        or name.endswith("blocks/queue/block.py")
        for name in files
    )
    findings: List[Finding] = []
    if always_200:
        findings.append(
            Finding("F1", "app/main.py", "GET /health is unconditional ok / always-200")
        )
    if not has_kernel:
        findings.append(
            Finding(
                "F1",
                "app/",
                "no cerebrum_product_kernel execute_action path; HTTP ok:true is not acceptance",
            )
        )
    if not has_update or not has_delete or not has_put or not has_http_delete:
        findings.append(
            Finding(
                "F6",
                "app/store.py",
                "missing update/delete persist (LotDesk-class CRUD hole)",
            )
        )
    if vendor_queue and not has_process:
        findings.append(
            Finding(
                "F5",
                "vendor/blocks/queue/block.py",
                "queue/workflow vendor is present but no persisted process transition",
            )
        )
    if not has_process:
        findings.append(
            Finding("F5", "app/work_queue.py", "hollow queue: no mark/process of pending items")
        )
    lotdesk = inspect_path(path)
    for item in lotdesk:
        findings.append(Finding(item.code, item.path, item.detail))

    outcomes: Dict[str, Any] = {}
    for name in OUTCOMES:
        if name in {OUTCOME_UPDATE_PERSISTS, OUTCOME_DELETE_PERSISTS} and not (
            has_update and has_delete
        ):
            outcomes[name] = {"status": "failed", "reason": "missing update/delete"}
        elif name == OUTCOME_QUEUE_ITEM_PROCESSED and not has_process:
            outcomes[name] = {"status": "failed", "reason": "hollow queue"}
        elif name == OUTCOME_REFUSED_ACTION_ERRORS and always_200:
            outcomes[name] = {"status": "failed", "reason": "always-200 / ok:true"}
        elif not has_kernel:
            outcomes[name] = {
                "status": "failed",
                "reason": "cannot perform through execute_action",
            }
        else:
            outcomes[name] = {
                "status": "failed",
                "reason": "LotDesk fixture is not a kernel product",
            }

    codes = [item.code for item in findings]
    failed = [name for name, item in outcomes.items() if item["status"] != "performed"]
    return {
        "ok": False,
        "gate": "lotdesk_domain_acceptance",
        "fixture": str(path),
        "outcomes": outcomes,
        "failed": failed,
        "performed": [name for name in OUTCOMES if name not in failed],
        "codes": codes,
        "findings": [asdict(item) for item in findings],
        "f1_present": "F1" in codes,
        "f5_present": "F5" in codes,
        "f6_present": "F6" in codes,
        "lotdesk": "fixture only; not patched",
    }


def reject_lotdesk_domain(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk-as-shipped fails S12. A hollow accept is a factory defect."""
    result = inspect_lotdesk_domain(explicit)
    if result["ok"]:
        raise AssertionError("GATE HOLLOW: LotDesk-as-shipped was accepted by S12")
    if len(result["failed"]) < 10:
        raise AssertionError(
            "GATE HOLLOW: LotDesk performed an S12 outcome it cannot: "
            + ",".join(result["performed"])
        )
    return result
