"""PRODUCT accept-payload / event_bus workflow contract for C-BRIEF.

The live VetCare Floor halt (sess_a4690fb3336c42fb, after #318) was
step_1. After #323 grounded the prepared publish shape, the wall moved
(sess_d70c18ef58ab48e6, tip 205f957) to step_2 booking. #325 grounded
step_2 / appointment_booking needles. Live tip d72b97f / #330
(sess_14e690829d1f4282) is back at:

    appointment_scheduling rejected a payload built from its own schema:
    workflow: step_1 (event_bus): error

#325 treated a factory execute wrap, or one prepared child, as enough.
WRITER then claimed done; PRODUCT still refused the first event_bus
child. This module is the one brief + harvest + harness contract:
EVERY event_bus child (step_1, step_2, and later) must be prepared in
source. A wrap / import is not keep-or-done. An LLM never writes these
rules.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from app.factory.build.schema_accept import (
    CHANNEL_SAMPLE,
    DATETIME_SAMPLE,
    DATE_SAMPLE,
    ENVELOPE_STATUS_SAMPLE,
    GENERIC_STR_SAMPLE,
    TIME_SAMPLE,
)

PRODUCT_ACCEPT_TEST = "test_every_capability_route_accepts_payload"
PRODUCT_ACCEPT_CHECK = "event_bus_workflow"
PRODUCT_EVENT_BUS_STEP_HALT = "workflow: step_N (event_bus): error"
PRODUCT_EVENT_BUS_STEP_CLASS = "schema sample refused (event_bus workflow step)"
PRODUCT_ACCEPT_EMPTY_CLASS = "accept-payload persisted nothing"
WRITER_EVENT_BUS_WORKFLOW_HALT = (
    "WRITER [check:event_bus_workflow] failed — plan binds "
    "workflow+event_bus without the prepared contract"
)

#: PRODUCT ``_sample_value`` for email-shaped names (not writer_behaviour).
PRODUCT_EMAIL_SAMPLE = "guest@example.com"

#: Store event_bus / notification channel after prepare_block_input.
EVENT_BUS_STEP_CHANNEL = "mcp"
EVENT_BUS_STEP_ACTION = "publish"

#: Keys PRODUCT / prepare map onto event_bus.topic when the sample has none.
EVENT_BUS_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")

#: Capability-id markers that CLI treats as appointment / booking / reminder
#: workflows. Live Floor aliases ``appointment_scheduling`` →
#: ``appointment_booking``; "booking" must bind the same contract.
REMINDER_STYLE_MARKERS = (
    "appointment",
    "scheduling",
    "booking",
    "reminder",
    "notification",
)

#: Live PRODUCT class after #323: the wall moved from step_1 to step_2.
#: Live tip d72b97f / #330: the wall is step_1 again (appointment_scheduling).
PRODUCT_EVENT_BUS_STEP_1_HALT = "workflow: step_1 (event_bus): error"
PRODUCT_EVENT_BUS_STEP_2_HALT = "workflow: step_2 (event_bus): error"
APPOINTMENT_SCHEDULING_STYLE = "appointment_scheduling"
APPOINTMENT_BOOKING_STYLE = "appointment_booking"

#: Tokens that mean the handler built an event_bus workflow child.
EVENT_BUS_STEP_TOKENS = (
    '"block": "event_bus"',
    "'block': 'event_bus'",
    '"block_id": "event_bus"',
    "'block_id': 'event_bus'",
    'execute("event_bus"',
    "execute('event_bus'",
    '["block"] = "event_bus"',
    "['block'] = 'event_bus'",
    '["block_id"] = "event_bus"',
    "['block_id'] = 'event_bus'",
)

#: Tokens that mean the handler invents a workflow ``steps`` list.
WORKFLOW_CHILD_TOKENS = (
    '"steps"',
    "'steps'",
    "steps =",
    "steps=",
    'execute("workflow"',
    "execute('workflow'",
)

#: Live CLI invention: forward the schema sample as the step input.
UNPREPARED_INPUT_FORWARD_TOKENS = (
    "'input': payload",
    '"input": payload',
    "'input': dict(payload)",
    '"input": dict(payload)',
    "'input': dict(sample)",
    '"input": dict(sample)',
    "input=payload",
    "input = payload",
    "input=dict(payload)",
    "input = dict(payload)",
    "input=dict(sample)",
    "input = dict(sample)",
)

PREPARE_BLOCK_INPUT_NEEDLE = "prepare_block_input"

#: Factory WRITER wrap prepares every execute() child, including step_2+.
FACTORY_WRAP_TOKENS = (
    "def _watched(block_id",
    "_prepare_block_input(",
)

#: MCP notify target on the prepared input (notification requires block/tool).
#: Use ``tool`` — ``input.block`` is also the workflow child discriminator,
#: so AST would treat the inner dict as a second unprepared event_bus step.
EVENT_BUS_MCP_BLOCK = "event_bus"
EVENT_BUS_MCP_TARGET_KEY = "tool"
FACTORY_GROUNDED_EVENT_BUS_SOURCE = "factory-grounded event_bus workflow"

#: Exact step FACTORY_CODE_CLI must emit (or let prepare_block_input shape).
PREPARED_EVENT_BUS_STEP_EXAMPLE = (
    "{\n"
    '  "block": "event_bus",\n'
    f'  "action": "{EVENT_BUS_STEP_ACTION}",\n'
    '  "input": {\n'
    '    "topic": "<non-empty str from event / reminder_type / record summary>",\n'
    '    "payload": {"reference": "<domain scalar — not the raw schema sample>"},\n'
    '    "message": "<non-empty str>",\n'
    f'    "channel": "{EVENT_BUS_STEP_CHANNEL}",\n'
    f'    "{EVENT_BUS_MCP_TARGET_KEY}": "{EVENT_BUS_MCP_BLOCK}"\n'
    "  }\n"
    "}"
)


class EventBusWorkflowHalt(ValueError):
    """WRITER must not claim done: a bound handler is still unprepared."""


def _capability_block_ids(item: Any) -> set:
    return {str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip()}


def _is_reminder_or_appointment_style(cid: str) -> bool:
    blob = str(cid or "").lower().replace("-", "_")
    return any(marker in blob for marker in REMINDER_STYLE_MARKERS)


def event_bus_workflow_capability_ids(compiled_or_inventory: Any) -> List[str]:
    """Capability ids that must receive the prepared event_bus step contract.

    A row binds the contract when it claims workflow + event_bus, or when
    the id is appointment / booking / reminder / notification style. CLI
    invents the pairing and multi-step workflows even when the plan only
    bound database — live ``appointment_booking`` at step_2 after #323.
    """
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    ids: List[str] = []
    for item in inventory or ():
        bids = _capability_block_ids(item)
        cid = str(getattr(item, "capability_id", "") or "")
        if not cid:
            continue
        both = "workflow" in bids and "event_bus" in bids
        style = _is_reminder_or_appointment_style(cid)
        if both or style:
            ids.append(cid)
    return ids


def declares_event_bus_workflow(compiled_or_inventory: Any) -> bool:
    return bool(event_bus_workflow_capability_ids(compiled_or_inventory))


def handler_constructs_event_bus_step(text: str) -> bool:
    blob = text or ""
    if "event_bus" not in blob:
        return False
    return any(token in blob for token in EVENT_BUS_STEP_TOKENS)


def handler_forwards_raw_sample(text: str) -> bool:
    blob = text or ""
    return any(token in blob for token in UNPREPARED_INPUT_FORWARD_TOKENS)


def handler_calls_prepare_block_input(text: str) -> bool:
    return PREPARE_BLOCK_INPUT_NEEDLE in (text or "")


def handler_has_factory_event_bus_wrap(text: str) -> bool:
    """True when the factory execute wrap prepares every workflow child."""
    blob = text or ""
    return all(token in blob for token in FACTORY_WRAP_TOKENS)


def _ast_str(node: Any) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ast_dict_map(node: Any) -> Optional[dict]:
    if not isinstance(node, ast.Dict):
        return None
    out: dict = {}
    for key, value in zip(node.keys, node.values):
        name = _ast_str(key)
        if name and value is not None:
            out[name] = value
    return out or None


def _ast_is_raw_sample(node: Any) -> bool:
    if isinstance(node, ast.Name) and node.id in {"payload", "sample"}:
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "dict" and node.args:
            return _ast_is_raw_sample(node.args[0])
    return False


def _ast_is_payload_get_without_fallback(node: Any) -> bool:
    """True when topic/message is ``payload.get(...)`` with no literal fallback.

    PRODUCT ``_sample_payload`` for appointment_scheduling has pet/date
    fields — not topic / event. ``payload.get('event')`` is None at
    accept-payload time and Store records step_1 (event_bus): error.
    """
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        if any(_ast_str(value) for value in node.values):
            return False
        return any(_ast_is_payload_get_without_fallback(value) for value in node.values)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return False
    return isinstance(func.value, ast.Name) and func.value.id in {"payload", "sample"}


def _ast_is_event_bus_block(pairs: dict) -> bool:
    return any(
        _ast_str(pairs.get(key)) == "event_bus"
        for key in ("block", "block_id", "name")
    )


def _ast_inner_prepared(inner: dict) -> bool:
    topic = inner.get("topic")
    has_topic = (
        topic is not None
        and not _ast_is_raw_sample(topic)
        and not _ast_is_payload_get_without_fallback(topic)
    )
    has_payload = isinstance(inner.get("payload"), ast.Dict)
    message = inner.get("message")
    has_message = (
        message is not None
        and not _ast_is_raw_sample(message)
        and not _ast_is_payload_get_without_fallback(message)
    )
    has_channel = _ast_str(inner.get("channel")) == EVENT_BUS_STEP_CHANNEL
    return bool(has_topic and has_payload and has_message and has_channel)


def _ast_step_is_prepared(pairs: dict) -> bool:
    if _ast_str(pairs.get("action")) != EVENT_BUS_STEP_ACTION:
        return False
    inp = pairs.get("input")
    if inp is None or _ast_is_raw_sample(inp):
        return False
    inner = _ast_dict_map(inp)
    if not inner:
        return False
    return _ast_inner_prepared(inner)


def event_bus_steps_from_handler(text: str) -> List[Tuple[int, bool]]:
    """``(step_N, prepared)`` for each event_bus child in source.

    ``step_N`` matches PRODUCT ``workflow: step_N (event_bus)`` when the
    children live in one steps list (database + event_bus → step_2).
    """
    blob = text or ""
    try:
        tree = ast.parse(blob)
    except SyntaxError:
        try:
            tree = ast.parse("def handle(payload):\n" + blob)
        except SyntaxError:
            return []
    seen: set = set()
    out: List[Tuple[int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        for idx, elt in enumerate(node.elts, start=1):
            pairs = _ast_dict_map(elt)
            if not pairs or not _ast_is_event_bus_block(pairs):
                continue
            key = id(elt)
            if key in seen:
                continue
            seen.add(key)
            out.append((idx, _ast_step_is_prepared(pairs)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict) or id(node) in seen:
            continue
        pairs = _ast_dict_map(node)
        if not pairs or not _ast_is_event_bus_block(pairs):
            continue
        seen.add(id(node))
        out.append((len(out) + 1, _ast_step_is_prepared(pairs)))
    return out


def handler_has_prepared_event_bus_step(text: str) -> bool:
    """True when source names the PRODUCT-prepared event_bus step keys."""
    blob = text or ""
    if "event_bus" not in blob:
        return False
    has_topic = '"topic"' in blob or "'topic'" in blob or "input.topic" in blob
    has_payload_dict = (
        '"payload": {' in blob
        or "'payload': {" in blob
        or "payload = {" in blob
        or "payload={" in blob
        or '"payload": dict' in blob
        or "'payload': dict" in blob
    )
    has_message = '"message"' in blob or "'message'" in blob or "input.message" in blob
    has_channel = (
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}"' in blob
        or f"'channel': '{EVENT_BUS_STEP_CHANNEL}'" in blob
        or f'channel="{EVENT_BUS_STEP_CHANNEL}"' in blob
        or f"channel='{EVENT_BUS_STEP_CHANNEL}'" in blob
        or f"channel={EVENT_BUS_STEP_CHANNEL!r}" in blob
    )
    has_action = (
        f'"action": "{EVENT_BUS_STEP_ACTION}"' in blob
        or f"'action': '{EVENT_BUS_STEP_ACTION}'" in blob
        or f'action="{EVENT_BUS_STEP_ACTION}"' in blob
        or f"action='{EVENT_BUS_STEP_ACTION}'" in blob
        or f"action={EVENT_BUS_STEP_ACTION!r}" in blob
    )
    return bool(has_topic and has_payload_dict and has_message and has_channel and has_action)


def handler_builds_unparsed_event_bus_workflow(text: str) -> bool:
    """True when source invents workflow children with event_bus but AST
    cannot see those dicts. Fail closed — dynamic step_1 construction is
    the live appointment_scheduling class after #325.
    """
    blob = text or ""
    if "event_bus" not in blob:
        return False
    if not any(token in blob for token in WORKFLOW_CHILD_TOKENS):
        return False
    if event_bus_steps_from_handler(blob):
        return False
    return handler_constructs_event_bus_step(blob)


def handler_satisfies_event_bus_contract(
    text: str,
    *,
    require_prepared_step: bool = False,
) -> bool:
    """Every event_bus child is prepared in source. Wrap is not keep/done.

    Importing ``prepare_block_input`` or emitting the factory execute wrap
    is not enough — CLI often forwards the schema sample as step_1
    (appointment_scheduling) or as step_2 (appointment_booking). A
    prepared sibling plus any unprepared event_bus child must fail.
    Unparsed dynamic construction must fail.

    ``require_prepared_step`` is the #331 hole: a templated
    ``execute(block_id, payload)`` loop has no event_bus dicts, so the
    AST check used to vacuous-pass. WRITER then claimed done; PRODUCT
    executed workflow with the schema sample and refused step_1.
    Appointment / booking / reminder handlers must ship the prepared
    step in source (factory-grounded emit, not an LLM stub).
    """
    steps = event_bus_steps_from_handler(text)
    if steps:
        return all(prepared for _idx, prepared in steps)
    if handler_builds_unparsed_event_bus_workflow(text):
        return False
    if require_prepared_step:
        return False
    if not handler_constructs_event_bus_step(text):
        return True
    if handler_forwards_raw_sample(text):
        return False
    return handler_has_prepared_event_bus_step(text)


def needs_grounded_event_bus_handler(
    capability_id: str,
    block_ids: Optional[Sequence[str]] = None,
) -> bool:
    """True when WRITER must emit the prepared event_bus step in source.

    Style-only ids (stakeholder_notification) without a bound event_bus
    stay on the generic template. Inventing an event_bus workflow child
    when the block is not vendored is how field_ops PRODUCT went red.
    """
    cid = str(capability_id or "")
    bids = {str(b) for b in (block_ids or ()) if str(b).strip()}
    if "event_bus" not in bids:
        return False
    return _is_reminder_or_appointment_style(cid) or "workflow" in bids


def event_bus_step_is_store_ready(data: Any) -> bool:
    """True when a workflow child input should not become step_N (event_bus): error.

    Live Store notify (after #314 channel=mcp) still refuses a schema sample
    that has no topic / payload / message, uses channel=email without ``to``,
    or omits MCP ``block``/``tool``. Domain columns at the top level are not
    an event_bus contract.
    """
    if not isinstance(data, dict):
        return False
    topic = data.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        return False
    if not isinstance(data.get("payload"), dict):
        return False
    message = data.get("message")
    if not (isinstance(message, str) and str(message).strip()):
        return False
    channel = str(data.get("channel") or "").strip().lower()
    if channel != EVENT_BUS_STEP_CHANNEL:
        return False
    target = data.get("block") or data.get("tool")
    if not (isinstance(target, str) and target.strip()):
        return False
    return True


def grounded_event_bus_topic(capability_id: str) -> str:
    blob = str(capability_id or "record").lower().replace("-", "_")
    if "remind" in blob or "notif" in blob:
        return "reminder.due"
    if "book" in blob:
        return "appointment.booked"
    if "appoint" in blob or "schedul" in blob:
        return "appointment.scheduled"
    return f"{blob or 'record'}.recorded"


def grounded_event_bus_handler_body(
    capability_id: str,
    block_ids: Optional[Sequence[str]] = None,
) -> str:
    """Deterministic handle() body: prepared event_bus step_1, no LLM.

    The live #332 VetCare halt (stub_rate≈0.833) used ``_templated_body``
    ``execute(block_id, payload)``. That vacuous-passed #331's AST check,
    then PRODUCT ran workflow with the schema sample and refused
    appointment_scheduling step_1. This body is the factory-grounded path.
    """
    topic = grounded_event_bus_topic(capability_id)
    message = topic.replace(".", " ")
    bids = [str(b) for b in (block_ids or ()) if str(b).strip()]
    others = [b for b in bids if b not in {"workflow", "event_bus"}]
    other_loop = ""
    if others:
        other_loop = (
            "    for block_id in BLOCK_IDS:\n"
            "        if block_id in ('workflow', 'event_bus'):\n"
            "            continue\n"
            "        result = execute(\n"
            "            block_id, payload, "
            "action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
            "        )\n"
            "        results[block_id] = result\n"
            "        if isinstance(result, dict) and (\n"
            '            result.get("status") == "error" or "error" in result\n'
            "        ):\n"
            "            errors[block_id] = str("
            "result.get(\"error\") or result)[:200]\n"
        )
    return (
        "    results = {}\n"
        "    errors = {}\n"
        "    steps = [{\n"
        '        "block": "event_bus",\n'
        f'        "action": "{EVENT_BUS_STEP_ACTION}",\n'
        "        \"input\": {\n"
        f'            "topic": {topic!r},\n'
        '            "payload": {"reference": payload.get("reference") '
        'or payload.get("pet_name") or "record"},\n'
        f'            "message": {message!r},\n'
        f'            "channel": "{EVENT_BUS_STEP_CHANNEL}",\n'
        f'            "tool": "{EVENT_BUS_MCP_BLOCK}",\n'
        "        },\n"
        "    }]\n"
        f"{other_loop}"
        "    if 'workflow' in BLOCK_IDS:\n"
        "        result = execute(\n"
        "            'workflow', {'steps': steps}, "
        "action=BLOCK_DEFAULT_ACTIONS.get('workflow') or 'run',\n"
        "        )\n"
        "        results['workflow'] = result\n"
        "        if isinstance(result, dict) and (\n"
        '            result.get("status") == "error" or "error" in result\n'
        "        ):\n"
        "            errors['workflow'] = str("
        "result.get(\"error\") or result)[:200]\n"
        "    if 'event_bus' in BLOCK_IDS:\n"
        "        result = execute(\n"
        f"            'event_bus', steps[0]['input'], "
        f"action=BLOCK_DEFAULT_ACTIONS.get('event_bus') or "
        f"'{EVENT_BUS_STEP_ACTION}',\n"
        "        )\n"
        "        results['event_bus'] = result\n"
        "        if isinstance(result, dict) and (\n"
        '            result.get("status") == "error" or "error" in result\n'
        "        ):\n"
        "            errors['event_bus'] = str("
        "result.get(\"error\") or result)[:200]\n"
        "    if errors:\n"
        "        return {\n"
        '            "ok": False,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "error": "; ".join('
        'f"{b}: {e}" for b, e in sorted(errors.items())),\n'
        '            "results": results,\n'
        "        }\n"
        "    stored = _persist_record(payload)\n"
        '    return {"ok": True, "capability": CAPABILITY_ID, '
        '"results": results, "stored": stored}'
    )


def _action_module_ids(root: Path) -> List[str]:
    actions = Path(root) / "app" / "actions"
    if not actions.is_dir():
        return []
    return [
        path.stem
        for path in sorted(actions.glob("*.py"))
        if path.name != "__init__.py" and not path.name.startswith("_")
    ]


def handler_ids_for_event_bus_check(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Bound inventory ids plus on-disk appointment/booking-style modules.

    Floor aliases (``appointment_booking`` vs ``appointment_scheduling``)
    must not slip the WRITER check because the plan used a different id.
    """
    ids = list(event_bus_workflow_capability_ids(compiled_or_inventory))
    seen = {str(cid).replace("-", "_") for cid in ids}
    base = Path(root)
    for cid in _action_module_ids(base):
        if cid in seen:
            continue
        if _is_reminder_or_appointment_style(cid):
            ids.append(cid)
            seen.add(cid)
            continue
        path = base / "app" / "actions" / f"{cid}.py"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if handler_constructs_event_bus_step(text):
            ids.append(cid)
            seen.add(cid)
    return ids


def event_bus_workflow_handler_errors(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Scan written handlers. Empty = the WRITER check is green."""
    ids = handler_ids_for_event_bus_check(root, compiled_or_inventory)
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    bids_by_cid = {
        str(getattr(item, "capability_id", "") or ""): list(
            getattr(item, "block_ids", None) or []
        )
        for item in inventory or ()
    }
    errors: List[str] = []
    base = Path(root)
    for cid in ids:
        path = base / "app" / "actions" / f"{str(cid).replace('-', '_')}.py"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        require = (
            needs_grounded_event_bus_handler(cid, bids_by_cid.get(cid, ()))
            or handler_constructs_event_bus_step(text)
            or handler_builds_unparsed_event_bus_workflow(text)
        )
        if handler_satisfies_event_bus_contract(
            text, require_prepared_step=require
        ):
            continue
        steps = event_bus_steps_from_handler(text)
        bad = [f"step_{idx}" for idx, prepared in steps if not prepared]
        where = (
            f"workflow: {', '.join(bad)} (event_bus): error"
            if bad
            else PRODUCT_EVENT_BUS_STEP_HALT
        )
        errors.append(
            f"{cid}: unprepared event_bus workflow step "
            f"({where}; {PRODUCT_EVENT_BUS_STEP_CLASS})"
        )
    return errors


def assert_event_bus_workflow_handlers(
    root: Path,
    compiled_or_inventory: Any,
) -> None:
    """Fail closed before WRITER claims done."""
    errors = event_bus_workflow_handler_errors(root, compiled_or_inventory)
    if errors:
        raise EventBusWorkflowHalt(
            WRITER_EVENT_BUS_WORKFLOW_HALT + ": " + "; ".join(errors)
        )


def workflow_accept_rules_text(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """BUILD cut: what PRODUCT will POST and the exact prepared step."""
    topic_keys = " / ".join(EVENT_BUS_TOPIC_KEYS[1:])
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    bound_lines: List[str] = []
    if named:
        bound_lines = [
            "These planned capabilities bind workflow and/or event_bus",
            "(appointment / scheduling / booking / reminders style) and MUST",
            "use the prepared step on EVERY event_bus child, including",
            f"{APPOINTMENT_SCHEDULING_STYLE} step_1 and {APPOINTMENT_BOOKING_STYLE} step_2+:",
            *[f"- {cid}" for cid in named],
            "",
        ]
    return "\n".join(
        [
            "PRODUCT gate (after WRITER writer_behaviour) — accept-payload:",
            f"The harness runs tests/test_routes.py::{PRODUCT_ACCEPT_TEST}.",
            "It POSTs /v1/{capability_id} with a payload built from that",
            "capability's own FIELDS + CONSTRAINTS (same idea as",
            "writer_behaviour; PRODUCT then executes the bound blocks).",
            "A route that returns ok:false fails with:",
            f"  {{capability}} rejected a payload built from its own schema: "
            f"{PRODUCT_EVENT_BUS_STEP_HALT}",
            f"Named class: {PRODUCT_EVENT_BUS_STEP_CLASS}; "
            f"{PRODUCT_ACCEPT_EMPTY_CLASS}.",
            "",
            *bound_lines,
            "PRODUCT schema-sample rules (roles_handlers._sample_payload):",
            "- CONSTRAINTS.allowed_values[0] when declared",
            f"- status / *_status → {ENVELOPE_STATUS_SAMPLE}",
            f"- channel / *_channel → {CHANNEL_SAMPLE} (never the word "
            f"{GENERIC_STR_SAMPLE})",
            f"- datetime / *_at / *_datetime → {DATETIME_SAMPLE}",
            f"- date / *_date → {DATE_SAMPLE}",
            f"- time / *_time → {TIME_SAMPLE}",
            f"- email-shaped names → {PRODUCT_EMAIL_SAMPLE}",
            f"- otherwise the word {GENERIC_STR_SAMPLE}",
            "",
            "That schema sample is NOT an event_bus input. When a capability",
            "binds workflow AND event_bus — or a reminders / appointment /",
            f"scheduling / booking-style id ({APPOINTMENT_SCHEDULING_STYLE} /",
            f"{APPOINTMENT_BOOKING_STYLE}) invents a workflow — do NOT set",
            "ANY step input to payload. An unprepared first child fails",
            f"PRODUCT as {PRODUCT_EVENT_BUS_STEP_1_HALT}. step_1 prepared +",
            f"step_2 raw still fails as {PRODUCT_EVENT_BUS_STEP_2_HALT}.",
            "The Store workflow records a child refusal as status=error — often",
            "only the banner string, no inner message.",
            "WRITER emits a factory-grounded prepared event_bus step for",
            "appointment / booking / reminder capabilities — do not burn",
            "rework on execute(block_id, payload) stubs, and do not",
            'execute("workflow", payload) with the raw schema sample.',
            "Construct each event_bus step (every child, including step_1",
            "and step_2+) in source. The factory execute wrap is a safety",
            "net, not permission to keep/done an unprepared child:",
            "- step['block'] = 'event_bus' (workflow reads block, not block_id)",
            f"- action={EVENT_BUS_STEP_ACTION} (BLOCK_DEFAULT_ACTIONS, keyword only)",
            f"- input.topic = non-empty str ({topic_keys} or a record summary)",
            "- input.payload = dict of domain scalars (not the raw sample alone)",
            "- input.message = non-empty str",
            f"- input.channel = {EVENT_BUS_STEP_CHANNEL!r} "
            f"(never {GENERIC_STR_SAMPLE!r}; {CHANNEL_SAMPLE!r} without `to` is not notify-ready)",
            f"- input.{EVENT_BUS_MCP_TARGET_KEY} = {EVENT_BUS_MCP_BLOCK!r} "
            "(MCP notify requires block/tool; the schema sample has neither)",
            "Exact prepared event_bus workflow step (copy this shape on",
            "EVERY event_bus child — step_1, step_2, and later):",
            PREPARED_EVENT_BUS_STEP_EXAMPLE,
            "Do not invent a second, unprepared event_bus child after a",
            "prepared step. Do not invent a stricter workflow the spec",
            "cannot express.",
        ]
    )


def workflow_accept_acceptance_line(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """ACCEPTANCE cut: PRODUCT harness check, not a coder decorative test."""
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    who = f" ({', '.join(named)})" if named else ""
    return (
        f"- PRODUCT accept-payload{who}: every event_bus step including "
        f"step_1 ({APPOINTMENT_SCHEDULING_STYLE} class) and step_2+ "
        f"({APPOINTMENT_BOOKING_STYLE} class) accepts the prepared "
        f"contract (topic, payload dict, message, "
        f"channel={EVENT_BUS_STEP_CHANNEL}, "
        f"action={EVENT_BUS_STEP_ACTION}) — never the raw schema sample "
        f"({PRODUCT_ACCEPT_TEST}; {PRODUCT_EVENT_BUS_STEP_HALT}; "
        f"{PRODUCT_EVENT_BUS_STEP_1_HALT}; "
        f"{PRODUCT_EVENT_BUS_STEP_2_HALT})  "
        f"[check:{PRODUCT_ACCEPT_CHECK}]"
    )


def workflow_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live CLI inventions that PRODUCT then refuses."""
    return "\n".join(
        [
            "- forwarding the PRODUCT schema sample as an event_bus workflow step input",
            "- setting an event_bus workflow step to 'input': payload or "
            '"input": payload (or input=dict(payload))',
            "- an unprepared step_1 (event_bus) — "
            f"{APPOINTMENT_SCHEDULING_STYLE} class fails PRODUCT as "
            f"{PRODUCT_EVENT_BUS_STEP_1_HALT}",
            "- a prepared step_1 plus an unprepared step_2 (event_bus) — "
            f"{APPOINTMENT_BOOKING_STYLE} class still fails PRODUCT as "
            f"{PRODUCT_EVENT_BUS_STEP_2_HALT}",
            "- treating one prepared event_bus child, a prepare_block_input "
            "import, or the factory execute wrap as keep/done while any "
            "child (including step_1) is still raw",
            f"- channel={GENERIC_STR_SAMPLE} or channel={CHANNEL_SAMPLE} without "
            f"`to` on an event_bus step (use channel={EVENT_BUS_STEP_CHANNEL})",
            f"- inventing {PRODUCT_EVENT_BUS_STEP_HALT} / "
            f"{PRODUCT_EVENT_BUS_STEP_1_HALT} / "
            f"{PRODUCT_EVENT_BUS_STEP_2_HALT} without the prepared "
            f"contract on EVERY event_bus child (topic, payload dict, message, "
            f"channel={EVENT_BUS_STEP_CHANNEL}, action={EVENT_BUS_STEP_ACTION})",
            "- execute(block_id, payload) stubs for appointment / booking / "
            "reminder capabilities (WRITER must emit the factory-grounded "
            "prepared event_bus step)",
            '- execute("workflow", payload) with the raw schema sample',
        ]
    )


def workflow_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"PRODUCT {PRODUCT_ACCEPT_TEST} POSTs a schema-sample payload then "
        f"runs bound blocks. A capability that binds workflow + event_bus "
        f"({APPOINTMENT_SCHEDULING_STYLE} / {APPOINTMENT_BOOKING_STYLE} / "
        f"reminders_notifications style) must prepare EACH event_bus step "
        f"including step_1 and step_2+ "
        f"(block=event_bus, action={EVENT_BUS_STEP_ACTION}, topic, "
        f"payload dict, message, channel={EVENT_BUS_STEP_CHANNEL}, "
        f"{EVENT_BUS_MCP_TARGET_KEY}={EVENT_BUS_MCP_BLOCK}) — never "
        f"forward the raw sample as 'input': payload. Unprepared steps fail as "
        f"{PRODUCT_EVENT_BUS_STEP_HALT!r}. An unprepared first child fails as "
        f"{PRODUCT_EVENT_BUS_STEP_1_HALT!r}. A prepared step_1 plus "
        f"an unprepared step_2 still fails as {PRODUCT_EVENT_BUS_STEP_2_HALT!r} "
        f"({PRODUCT_EVENT_BUS_STEP_CLASS}). The factory wrap is not keep/done. "
        f"WRITER emits a factory-grounded prepared event_bus step — do not "
        f'execute("workflow", payload) with the raw schema sample. '
        f"Exact shape: "
        f'{{"block": "event_bus", "action": "{EVENT_BUS_STEP_ACTION}", '
        f'"input": {{"topic": "<str>", "payload": {{}}, "message": "<str>", '
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}", '
        f'"{EVENT_BUS_MCP_TARGET_KEY}": "{EVENT_BUS_MCP_BLOCK}"}}}}.'
    )


def workflow_accept_needles() -> Sequence[str]:
    """Needles lint requires when a capability declares event_bus workflows."""
    return (
        PRODUCT_ACCEPT_TEST,
        PRODUCT_EVENT_BUS_STEP_HALT,
        PRODUCT_EVENT_BUS_STEP_1_HALT,
        PRODUCT_EVENT_BUS_STEP_2_HALT,
        PRODUCT_EVENT_BUS_STEP_CLASS,
        APPOINTMENT_SCHEDULING_STYLE,
        APPOINTMENT_BOOKING_STYLE,
        f"[check:{PRODUCT_ACCEPT_CHECK}]",
        f"channel={EVENT_BUS_STEP_CHANNEL}",
        "never the raw schema sample",
        f"action={EVENT_BUS_STEP_ACTION}",
        "payload dict",
        "input.topic",
        "input.message",
        "'input': payload",
        "not the raw schema sample",
        "every event_bus",
        "step_1",
        "step_2",
        "keep/done",
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}"',
        f'"action": "{EVENT_BUS_STEP_ACTION}"',
        "factory-grounded",
        'execute("workflow", payload)',
        "execute(block_id, payload)",
        "input.tool",
        f'"tool": "{EVENT_BUS_MCP_BLOCK}"',
    )


def inventory_block_ids(inventory: Iterable[Any]) -> List[str]:
    """Flat claimed block ids (tests / lint helpers)."""
    out: List[str] = []
    for item in inventory or ():
        out.extend(str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip())
    return out
