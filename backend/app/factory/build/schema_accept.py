"""Schema-accept contract for C-BRIEF + WRITER ``writer_behaviour``.

The live VetCare Floor halt (sess_91553364089d4970) was:

    WRITER gate 'writer_behaviour' failed: no capability accepted its own schema.

That sentence is the baseline phase of ``writer_behaviour``: the harness
POSTs ``/v1/{capability_id}`` with a payload built from that capability's
own ``FIELDS`` + ``CONSTRAINTS``. Every capability refused before a block
was reached.

This module is the one brief-facing contract. The compiler fills BUILD and
ACCEPTANCE from it. The coding-agent system brief cites it. An LLM never
writes these rules. Sampling literals must stay aligned with the probe in
``writer_behaviour.BEHAVIOUR_PROBE`` (``_value`` / ``_payload``).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES
from app.factory.build.writer_behaviour import GATE_NAME, SCHEMA_HALT

SCHEMA_ACCEPT_GATE = GATE_NAME
SCHEMA_ACCEPT_HALT = SCHEMA_HALT
SCHEMA_ACCEPT_CHECK = "writer_behaviour"

#: Probe / brief envelope. ``status`` without allowed_values samples as this.
ENVELOPE_STATUS_SAMPLE = ENVELOPE_STATUS_VALUES[0]  # open

#: Probe ``_value`` for ``channel`` / ``*_channel`` when no allowed_values.
CHANNEL_SAMPLE = "email"

#: Probe ``_value`` for a generic str field with no other heuristic.
GENERIC_STR_SAMPLE = "sample"

#: Probe temporal samples (must not be the word ``sample``).
DATETIME_SAMPLE = "2026-09-03T10:00:00"
DATE_SAMPLE = "2026-09-03"
TIME_SAMPLE = "10:00:00"
EMAIL_SAMPLE = "sample@example.com"

#: Minimum envelope every capability carries after ``ensure_record_envelope``.
ENVELOPE_ACCEPT_SAMPLE: Dict[str, Any] = {
    "reference": GENERIC_STR_SAMPLE,
    "status": ENVELOPE_STATUS_SAMPLE,
}


def schema_accept_rules_text() -> str:
    """BUILD cut: what the WRITER gate will POST and what accept means."""
    vocab = " | ".join(ENVELOPE_STATUS_VALUES)
    sample_json = (
        '{"reference": "'
        + GENERIC_STR_SAMPLE
        + '", "status": "'
        + ENVELOPE_STATUS_SAMPLE
        + '"}'
    )
    return "\n".join(
        [
            f"WRITER gate {SCHEMA_ACCEPT_GATE} (baseline, before PRODUCT):",
            "The harness POSTs /v1/{capability_id} with a payload built from",
            "that capability's own FIELDS + CONSTRAINTS. A route or handle()",
            "that refuses that payload before execute() fails the gate with:",
            f"  {SCHEMA_ACCEPT_HALT}",
            "Accept means HTTP 200 and not ok:false before a block is reached.",
            "If you need a field, type, or vocabulary, declare it on the spec",
            "so the sample includes it (allowed_values[0], bounds, format).",
            "Do not invent a second, stricter contract the spec cannot express.",
            "Do not require block-specific keys (topic, sql/table, file paths,",
            "team_id, channel, steps) from the caller — construct those inputs.",
            "",
            "Sampling rules (must match writer_behaviour probe _value):",
            "- CONSTRAINTS.allowed_values[0] when declared",
            f"- status / *_status → {ENVELOPE_STATUS_SAMPLE} (envelope {vocab})",
            f"- channel / *_channel → {CHANNEL_SAMPLE} (never the word {GENERIC_STR_SAMPLE})",
            f"- datetime / *_at / *_datetime → {DATETIME_SAMPLE}",
            f"- date / *_date → {DATE_SAMPLE}",
            f"- time / *_time → {TIME_SAMPLE}",
            f"- email-shaped names → {EMAIL_SAMPLE}",
            "- int/float → min if set else 1 (min=0 samples as 0 in the probe)",
            "- bool → false",
            f"- otherwise the word {GENERIC_STR_SAMPLE}",
            "",
            "Every capability's model already carries this envelope; the gate",
            "POSTs it plus samples for any extra FIELDS you declare:",
            sample_json,
        ]
    )


def schema_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    return (
        f"- every capability accepts a POST built from its own FIELDS/"
        f"CONSTRAINTS ({SCHEMA_ACCEPT_GATE} baseline)  "
        f"[check:{SCHEMA_ACCEPT_CHECK}]"
    )


def schema_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"WRITER gate {SCHEMA_ACCEPT_GATE} POSTs a payload built from each "
        "capability's own FIELDS + CONSTRAINTS (allowed_values[0], "
        f"status={ENVELOPE_STATUS_SAMPLE}, channel={CHANNEL_SAMPLE}, "
        f"generic str={GENERIC_STR_SAMPLE!r}). Every handler must accept "
        "that payload. Declaring a stricter contract than the spec produces "
        f"{SCHEMA_ACCEPT_HALT!r}."
    )


def probe_sample_value(
    name: str,
    *,
    annotation: str = "str",
    constraints: Optional[Mapping[str, Any]] = None,
) -> Any:
    """A value the ``writer_behaviour`` baseline probe will send for *name*.

    Mirrors ``BEHAVIOUR_PROBE._value``. Keep literals in sync with that
    string; ``test_schema_accept_contract`` fails the suite on drift.
    """
    con = dict(constraints or {})
    allowed = con.get("allowed_values")
    if allowed:
        return allowed[0]
    kind = str(annotation or "str").replace("Optional[", "").replace("]", "").strip()
    kind_l = kind.lower().replace("datetime.", "").replace(" ", "")
    if kind in ("int", "float") or kind_l in ("int", "float"):
        low, high = con.get("min"), con.get("max")
        if low is not None:
            return low
        if high is not None:
            return high if high < 1 else 1
        return 1
    if kind == "bool" or kind_l == "bool":
        return False
    n = str(name or "").lower()
    if "email" in n:
        return EMAIL_SAMPLE
    fmt = str(con.get("format") or "").lower().replace("-", "")
    if (
        kind_l in ("datetime", "timestamp")
        or fmt in ("datetime", "timestamp", "iso8601")
        or n.endswith("_at")
        or n.endswith("_datetime")
    ):
        return DATETIME_SAMPLE
    if kind_l == "date" or fmt == "date" or n.endswith("_date"):
        return DATE_SAMPLE
    if kind_l == "time" or fmt == "time" or n.endswith("_time") or n == "time":
        return TIME_SAMPLE
    if n == "status" or n.endswith("_status"):
        return ENVELOPE_STATUS_SAMPLE
    if n == "channel" or n.endswith("_channel"):
        return CHANNEL_SAMPLE
    return GENERIC_STR_SAMPLE


def probe_sample_payload(
    fields: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Probe payload for a spec's field list (name / type / constraints)."""
    out: Dict[str, Any] = {}
    for field in fields or ():
        if not isinstance(field, Mapping):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        constraints = {
            k: field[k]
            for k in ("allowed_values", "min", "max", "format")
            if field.get(k) is not None
        }
        out[name] = probe_sample_value(
            name,
            annotation=str(field.get("type") or "str"),
            constraints=constraints,
        )
    return out
