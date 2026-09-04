"""PRODUCT accept-payload / event_bus workflow contract for C-BRIEF.

The live VetCare Floor halt (sess_e94ddfa797ea4a45) was:

    PRODUCT (pilot-marked suite): suite is red: schema sample refused;
    schema sample refused (event_bus workflow step);
    accept-payload persisted nothing — FAILED

    reminders_and_notifications rejected a payload built from its own
    schema: workflow: step_2 (event_bus): error.

That is the same overnight PRODUCT class as appointment_scheduling
``workflow: step_1 (event_bus): error`` (#315) then step_2. The suite
POSTs ``test_every_capability_route_accepts_payload`` — a payload built
from the capability's own FIELDS + CONSTRAINTS — then the handler's
workflow child ``event_bus`` refuses the raw sample.

This module is the one brief-facing contract. The compiler fills BUILD
and ACCEPTANCE from it. The coding-agent system brief cites it. An LLM
never writes these rules. Sampling literals stay aligned with PRODUCT
``roles_handlers._sample_payload`` / ``_sample_value``.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence

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

#: PRODUCT ``_sample_value`` for email-shaped names (not writer_behaviour).
PRODUCT_EMAIL_SAMPLE = "guest@example.com"

#: Store event_bus / notification channel after prepare_block_input.
EVENT_BUS_STEP_CHANNEL = "mcp"
EVENT_BUS_STEP_ACTION = "publish"

#: Keys PRODUCT / prepare map onto event_bus.topic when the sample has none.
EVENT_BUS_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")


def event_bus_workflow_capability_ids(compiled_or_inventory: Any) -> List[str]:
    """Capability ids that bind workflow + event_bus on the same row."""
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    ids: List[str] = []
    for item in inventory or ():
        bids = {str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip()}
        if "workflow" in bids and "event_bus" in bids:
            cid = str(getattr(item, "capability_id", "") or "")
            if cid:
                ids.append(cid)
    return ids


def declares_event_bus_workflow(compiled_or_inventory: Any) -> bool:
    return bool(event_bus_workflow_capability_ids(compiled_or_inventory))


def workflow_accept_rules_text() -> str:
    """BUILD cut: what PRODUCT will POST and what an event_bus step must be."""
    topic_keys = " / ".join(EVENT_BUS_TOPIC_KEYS[1:])
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
            "When a capability binds workflow AND event_bus, do NOT forward",
            "that schema sample as an event_bus workflow step input. The",
            "Store workflow records a child refusal as status=error — often",
            "only the banner string, no inner message.",
            "Construct each event_bus step (or let prepare_block_input shape",
            "coder-built steps — never return them unchanged):",
            "- step['block'] = 'event_bus' (workflow reads block, not block_id)",
            f"- action={EVENT_BUS_STEP_ACTION} (BLOCK_DEFAULT_ACTIONS, keyword only)",
            f"- input.topic = non-empty str ({topic_keys} or a record summary)",
            "- input.payload = dict of domain scalars (not the raw sample alone)",
            "- input.message = non-empty str",
            f"- input.channel = {EVENT_BUS_STEP_CHANNEL!r} "
            f"(never {GENERIC_STR_SAMPLE!r}; {CHANNEL_SAMPLE!r} without `to` is not notify-ready)",
            "Do not invent a second, stricter workflow the spec cannot express.",
        ]
    )


def workflow_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: PRODUCT harness check, not a coder decorative test."""
    return (
        f"- PRODUCT accept-payload: every workflow step whose block is "
        f"event_bus accepts the prepared contract (topic, payload dict, "
        f"message, channel={EVENT_BUS_STEP_CHANNEL}, "
        f"action={EVENT_BUS_STEP_ACTION}) — never the raw schema sample "
        f"({PRODUCT_ACCEPT_TEST}; {PRODUCT_EVENT_BUS_STEP_HALT})  "
        f"[check:{PRODUCT_ACCEPT_CHECK}]"
    )


def workflow_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"PRODUCT {PRODUCT_ACCEPT_TEST} POSTs a schema-sample payload then "
        f"runs bound blocks. A capability that binds workflow + event_bus "
        f"must prepare each event_bus step "
        f"(block=event_bus, action={EVENT_BUS_STEP_ACTION}, topic, "
        f"payload dict, message, channel={EVENT_BUS_STEP_CHANNEL}) — never "
        f"forward the raw sample. Unprepared steps fail as "
        f"{PRODUCT_EVENT_BUS_STEP_HALT!r} "
        f"({PRODUCT_EVENT_BUS_STEP_CLASS})."
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
    )


def inventory_block_ids(inventory: Iterable[Any]) -> List[str]:
    """Flat claimed block ids (tests / lint helpers)."""
    out: List[str] = []
    for item in inventory or ():
        out.extend(str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip())
    return out
