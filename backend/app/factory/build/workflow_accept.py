"""PRODUCT accept-payload / event_bus workflow contract for C-BRIEF.

The live VetCare Floor halt (sess_a4690fb3336c42fb, after #318) was
step_1. After #323 grounded the prepared publish shape, the wall moved
(sess_d70c18ef58ab48e6, tip 205f957):

    PRODUCT (pilot-marked suite): suite is red: schema sample refused;
    schema sample refused (event_bus workflow step);
    accept-payload persisted nothing — FAILED

    appointment_booking rejected a payload built from its own schema:
    workflow: step_2 (event_bus): error

#323 validated a single prepared publish step / ``prepare_block_input``
import. CLI then shipped a prepared step_1 plus an unprepared step_2, or
aliased ``appointment_scheduling`` → ``appointment_booking``. This
module is the one brief + harvest + harness contract: EVERY event_bus
child (step_2+) must be prepared. An LLM never writes these rules.
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
PRODUCT_EVENT_BUS_STEP_2_HALT = "workflow: step_2 (event_bus): error"
APPOINTMENT_BOOKING_STYLE = "appointment_booking"

#: Tokens that mean the handler built an event_bus workflow child.
EVENT_BUS_STEP_TOKENS = (
    '"block": "event_bus"',
    "'block': 'event_bus'",
    '"block_id": "event_bus"',
    "'block_id': 'event_bus'",
    'execute("event_bus"',
    "execute('event_bus'",
)

#: Live CLI invention: forward the schema sample as the step input.
UNPREPARED_INPUT_FORWARD_TOKENS = (
    "'input': payload",
    '"input": payload',
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

#: Exact step FACTORY_CODE_CLI must emit (or let prepare_block_input shape).
PREPARED_EVENT_BUS_STEP_EXAMPLE = (
    "{\n"
    '  "block": "event_bus",\n'
    f'  "action": "{EVENT_BUS_STEP_ACTION}",\n'
    '  "input": {\n'
    '    "topic": "<non-empty str from event / reminder_type / record summary>",\n'
    '    "payload": {"reference": "<domain scalar — not the raw schema sample>"},\n'
    '    "message": "<non-empty str>",\n'
    f'    "channel": "{EVENT_BUS_STEP_CHANNEL}"\n'
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


def _ast_is_event_bus_block(pairs: dict) -> bool:
    return any(
        _ast_str(pairs.get(key)) == "event_bus"
        for key in ("block", "block_id", "name")
    )


def _ast_inner_prepared(inner: dict) -> bool:
    topic = inner.get("topic")
    has_topic = topic is not None and not _ast_is_raw_sample(topic)
    has_payload = isinstance(inner.get("payload"), ast.Dict)
    message = inner.get("message")
    has_message = message is not None and not _ast_is_raw_sample(message)
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
    try:
        tree = ast.parse(text or "")
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


def handler_satisfies_event_bus_contract(text: str) -> bool:
    """Every event_bus child is prepared, or the factory wrap will prepare it.

    Importing ``prepare_block_input`` is not enough — CLI often prepares
    step_1 (database) and still forwards the schema sample as step_2
    (event_bus). A prepared step_1 plus an unprepared step_2 must fail.
    """
    if handler_has_factory_event_bus_wrap(text):
        return True
    steps = event_bus_steps_from_handler(text)
    if steps:
        return all(prepared for _idx, prepared in steps)
    if not handler_constructs_event_bus_step(text):
        return True
    if handler_forwards_raw_sample(text):
        return False
    return handler_has_prepared_event_bus_step(text)


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
        if handler_satisfies_event_bus_contract(text):
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
            "(appointment / booking / reminders style) and MUST use the",
            "prepared step on EVERY event_bus child, including step_2+:",
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
            f"booking-style id ({APPOINTMENT_BOOKING_STYLE} aliases",
            "appointment_scheduling) invents a multi-step workflow — do NOT",
            "set ANY step input to payload. step_1 prepared + step_2 raw",
            f"still fails PRODUCT as {PRODUCT_EVENT_BUS_STEP_2_HALT}.",
            "The Store workflow records a child refusal as status=error — often",
            "only the banner string, no inner message.",
            "Construct each event_bus step (every child, including step_2+)",
            "or let the factory execute wrap call prepare_block_input on the",
            "workflow payload (never return coder-built steps unchanged):",
            "- step['block'] = 'event_bus' (workflow reads block, not block_id)",
            f"- action={EVENT_BUS_STEP_ACTION} (BLOCK_DEFAULT_ACTIONS, keyword only)",
            f"- input.topic = non-empty str ({topic_keys} or a record summary)",
            "- input.payload = dict of domain scalars (not the raw sample alone)",
            "- input.message = non-empty str",
            f"- input.channel = {EVENT_BUS_STEP_CHANNEL!r} "
            f"(never {GENERIC_STR_SAMPLE!r}; {CHANNEL_SAMPLE!r} without `to` is not notify-ready)",
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
        f"step_2+ ({APPOINTMENT_BOOKING_STYLE} class) accepts the prepared "
        f"contract (topic, payload dict, message, "
        f"channel={EVENT_BUS_STEP_CHANNEL}, "
        f"action={EVENT_BUS_STEP_ACTION}) — never the raw schema sample "
        f"({PRODUCT_ACCEPT_TEST}; {PRODUCT_EVENT_BUS_STEP_HALT}; "
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
            "- a prepared step_1 plus an unprepared step_2 (event_bus) — "
            f"{APPOINTMENT_BOOKING_STYLE} class still fails PRODUCT as "
            f"{PRODUCT_EVENT_BUS_STEP_2_HALT}",
            f"- channel={GENERIC_STR_SAMPLE} or channel={CHANNEL_SAMPLE} without "
            f"`to` on an event_bus step (use channel={EVENT_BUS_STEP_CHANNEL})",
            f"- inventing {PRODUCT_EVENT_BUS_STEP_HALT} / "
            f"{PRODUCT_EVENT_BUS_STEP_2_HALT} without the prepared "
            f"contract on EVERY event_bus child (topic, payload dict, message, "
            f"channel={EVENT_BUS_STEP_CHANNEL}, action={EVENT_BUS_STEP_ACTION})",
        ]
    )


def workflow_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"PRODUCT {PRODUCT_ACCEPT_TEST} POSTs a schema-sample payload then "
        f"runs bound blocks. A capability that binds workflow + event_bus "
        f"(appointment_scheduling / {APPOINTMENT_BOOKING_STYLE} / "
        f"reminders_notifications style) must prepare EACH event_bus step "
        f"including step_2+ "
        f"(block=event_bus, action={EVENT_BUS_STEP_ACTION}, topic, "
        f"payload dict, message, channel={EVENT_BUS_STEP_CHANNEL}) — never "
        f"forward the raw sample as 'input': payload. A prepared step_1 plus "
        f"an unprepared step_2 still fails as {PRODUCT_EVENT_BUS_STEP_2_HALT!r} "
        f"({PRODUCT_EVENT_BUS_STEP_CLASS}). Exact shape: "
        f'{{"block": "event_bus", "action": "{EVENT_BUS_STEP_ACTION}", '
        f'"input": {{"topic": "<str>", "payload": {{}}, "message": "<str>", '
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}"}}}}.'
    )


def workflow_accept_needles() -> Sequence[str]:
    """Needles lint requires when a capability declares event_bus workflows."""
    return (
        PRODUCT_ACCEPT_TEST,
        PRODUCT_EVENT_BUS_STEP_HALT,
        PRODUCT_EVENT_BUS_STEP_2_HALT,
        PRODUCT_EVENT_BUS_STEP_CLASS,
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
        "step_2",
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}"',
        f'"action": "{EVENT_BUS_STEP_ACTION}"',
    )


def inventory_block_ids(inventory: Iterable[Any]) -> List[str]:
    """Flat claimed block ids (tests / lint helpers)."""
    out: List[str] = []
    for item in inventory or ():
        out.extend(str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip())
    return out
