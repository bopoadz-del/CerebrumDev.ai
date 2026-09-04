"""PRODUCT accept-payload / event_bus workflow contract for C-BRIEF.

The live VetCare Floor halt (sess_a4690fb3336c42fb, after #318) was:

    PRODUCT (pilot-marked suite): suite is red: schema sample refused;
    schema sample refused (event_bus workflow step);
    accept-payload persisted nothing — FAILED

    appointment_scheduling rejected a payload built from its own schema:
    workflow: step_1 (event_bus): error
    reminders_notifications … same class.

#318 named the contract. FACTORY_CODE_CLI still invented unprepared
``{'block': 'event_bus', 'input': payload}`` steps and harvest treated
any event_bus mention as keepable, so the factory wrapper never ran
``prepare_block_input``. This module is the one brief + harvest +
harness contract. An LLM never writes these rules. Sampling literals
stay aligned with PRODUCT ``roles_handlers._sample_payload``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

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

#: Capability-id markers that CLI treats as appointment / reminder workflows.
REMINDER_STYLE_MARKERS = (
    "appointment",
    "scheduling",
    "reminder",
    "notification",
)

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

    A row binds the contract when it claims workflow + event_bus, or when a
    reminder / appointment-style id claims either block (CLI invents the
    pairing — live ``reminders_notifications`` / ``appointment_scheduling``).
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
        style = _is_reminder_or_appointment_style(cid) and (
            "event_bus" in bids or "workflow" in bids
        )
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
    """Factory wrap (prepare_block_input) or an explicit prepared step."""
    if handler_calls_prepare_block_input(text):
        return True
    if not handler_constructs_event_bus_step(text):
        return True
    return handler_has_prepared_event_bus_step(text)


def event_bus_workflow_handler_errors(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Scan written handlers. Empty = the WRITER check is green."""
    ids = event_bus_workflow_capability_ids(compiled_or_inventory)
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
        errors.append(
            f"{cid}: unprepared event_bus workflow step "
            f"({PRODUCT_EVENT_BUS_STEP_HALT}; {PRODUCT_EVENT_BUS_STEP_CLASS})"
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
            "(appointment / reminders style) and MUST use the prepared step",
            "below — PRODUCT POSTs the schema sample to each of them:",
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
            "binds workflow AND event_bus — or a reminders / appointment-style",
            "id invents a workflow child — do NOT set step input to payload.",
            "The Store workflow records a child refusal as status=error — often",
            "only the banner string, no inner message.",
            "Construct each event_bus step (or call prepare_block_input —",
            "never return coder-built steps unchanged):",
            "- step['block'] = 'event_bus' (workflow reads block, not block_id)",
            f"- action={EVENT_BUS_STEP_ACTION} (BLOCK_DEFAULT_ACTIONS, keyword only)",
            f"- input.topic = non-empty str ({topic_keys} or a record summary)",
            "- input.payload = dict of domain scalars (not the raw sample alone)",
            "- input.message = non-empty str",
            f"- input.channel = {EVENT_BUS_STEP_CHANNEL!r} "
            f"(never {GENERIC_STR_SAMPLE!r}; {CHANNEL_SAMPLE!r} without `to` is not notify-ready)",
            "Exact prepared event_bus workflow step (copy this shape):",
            PREPARED_EVENT_BUS_STEP_EXAMPLE,
            "Do not invent a second, stricter workflow the spec cannot express.",
        ]
    )


def workflow_accept_acceptance_line(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """ACCEPTANCE cut: PRODUCT harness check, not a coder decorative test."""
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    who = f" ({', '.join(named)})" if named else ""
    return (
        f"- PRODUCT accept-payload{who}: every workflow step whose block is "
        f"event_bus accepts the prepared contract (topic, payload dict, "
        f"message, channel={EVENT_BUS_STEP_CHANNEL}, "
        f"action={EVENT_BUS_STEP_ACTION}) — never the raw schema sample "
        f"({PRODUCT_ACCEPT_TEST}; {PRODUCT_EVENT_BUS_STEP_HALT})  "
        f"[check:{PRODUCT_ACCEPT_CHECK}]"
    )


def workflow_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live CLI inventions that PRODUCT then refuses."""
    return "\n".join(
        [
            "- forwarding the PRODUCT schema sample as an event_bus workflow step input",
            "- setting an event_bus workflow step to 'input': payload or "
            '"input": payload (or input=dict(payload))',
            f"- channel={GENERIC_STR_SAMPLE} or channel={CHANNEL_SAMPLE} without "
            f"`to` on an event_bus step (use channel={EVENT_BUS_STEP_CHANNEL})",
            f"- inventing {PRODUCT_EVENT_BUS_STEP_HALT} without the prepared "
            f"contract (topic, payload dict, message, "
            f"channel={EVENT_BUS_STEP_CHANNEL}, action={EVENT_BUS_STEP_ACTION})",
        ]
    )


def workflow_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"PRODUCT {PRODUCT_ACCEPT_TEST} POSTs a schema-sample payload then "
        f"runs bound blocks. A capability that binds workflow + event_bus "
        f"(appointment_scheduling / reminders_notifications style) "
        f"must prepare each event_bus step "
        f"(block=event_bus, action={EVENT_BUS_STEP_ACTION}, topic, "
        f"payload dict, message, channel={EVENT_BUS_STEP_CHANNEL}) — never "
        f"forward the raw sample as 'input': payload. Unprepared steps fail as "
        f"{PRODUCT_EVENT_BUS_STEP_HALT!r} "
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
        PRODUCT_EVENT_BUS_STEP_CLASS,
        f"[check:{PRODUCT_ACCEPT_CHECK}]",
        f"channel={EVENT_BUS_STEP_CHANNEL}",
        "never the raw schema sample",
        f"action={EVENT_BUS_STEP_ACTION}",
        "payload dict",
        "input.topic",
        "input.message",
        "'input': payload",
        "not the raw schema sample",
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}"',
        f'"action": "{EVENT_BUS_STEP_ACTION}"',
    )


def inventory_block_ids(inventory: Iterable[Any]) -> List[str]:
    """Flat claimed block ids (tests / lint helpers)."""
    out: List[str] = []
    for item in inventory or ():
        out.extend(str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip())
    return out
