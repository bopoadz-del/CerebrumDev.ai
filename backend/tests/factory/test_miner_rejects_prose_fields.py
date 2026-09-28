"""A field the miner reports must be one the handler actually reads.

The miner has an "X is missing" pattern (for error strings like VetConnect's
"pet_id is missing and must be a non-empty string"). It is unanchored, so it
also fires on prose: a handler whose refusal says "any of the four is missing"
yielded the field "four", and a docstring about "figures" yielded "figure".
Those junk fields went into the contract, the route then 422'd on
"unknown field(s): figure, four", and TESTER billed it FACTORY -- a real
factory bug that cost the writer a rework round every time it re-mined
(Cerebrum VenueOps, ledger STEP 21).

The rule (owner): the spec declares the fields; the miner may confirm a
declaration against the handler, never invent one from a sentence. A
prose-pattern capture is kept only when the handler reads it as a payload key.
"""

from __future__ import annotations

from app.factory.build.block_inputs import handler_required_fields

HANDLER = '''
"""Ops dashboards. The four figures the venue director reads.

NEVER reports a figure it did not compute from stored records.
"""

def handle(payload):
    if "metric" not in payload:
        raise ValueError("metric is required")
    scope = payload.get("cost_scope")
    if scope is None:
        # Refusal prose the miner must not read as field names:
        raise ValueError("any of the four is missing; provide the figure")
    return {"ok": True}
'''


def test_prose_words_are_not_mined_as_fields():
    fields = set(handler_required_fields(HANDLER))
    assert "four" not in fields, f"'four' mined from prose: {sorted(fields)}"
    assert "figure" not in fields, f"'figure' mined from prose: {sorted(fields)}"


def test_real_required_fields_are_still_mined():
    fields = set(handler_required_fields(HANDLER))
    assert "metric" in fields, f"real required field 'metric' dropped: {sorted(fields)}"


def test_vetconnect_error_string_field_survives_when_read():
    """The pattern's real purpose: 'pet_id is missing' when pet_id is a key."""
    handler = '''
def handle(payload):
    pet_id = payload.get("pet_id")
    if not pet_id:
        raise ValueError("pet_id is missing and must be a non-empty string")
    return {"ok": True}
'''
    assert "pet_id" in set(handler_required_fields(handler))
