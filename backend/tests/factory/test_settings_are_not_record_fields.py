"""An operator's settings are not an entity's fields.

Live:

    TESTER failed -- suite_red: FAILED
    tests/test_models.py::test_every_model_round_trips - KeyError: 'GOOGLE_CLIENT_ID'

The Factory-written test does ``for key in record: assert fetched[key] == ...``
with ``record`` built from the compiled spec's fields. A connector checked its
own required SETTINGS with the same loop shape a handler uses to check
required FIELDS, so the roster miner put the operator's credentials into the
entity's spec; the test saved them as a record, the model had no such column,
and ``fetched[key]`` raised. The writer's own rework note named it: "the
compiled spec's entity declares [the credentials], mined from the connector's
own settings".

The first diagnosis of this failure was an ``os.environ`` read. It was wrong,
and these tests pin the real mechanism so it cannot be re-guessed.

Nothing here names a real credential. The rule is a SHAPE -- an ALL_CAPS
identifier is a constant or configuration key by the convention every Python
file already follows -- so the names below are ones no product declares.
"""

from __future__ import annotations

import textwrap

import pytest

from app.factory.build.block_inputs import (
    _usable_align_name,
    align_spec_to_handler_source,
    handler_required_fields,
    required_fields_from_rosters,
)

_CONNECTOR = textwrap.dedent(
    '''
    REQUIRED_SETTINGS = ["ZZ_CLIENT_ID", "ZZ_CLIENT_SECRET", "ZZ_REFRESH_TOKEN"]


    def handle(payload, context=None):
        for key in REQUIRED_SETTINGS:
            if key not in payload:
                return {"status": "error", "error": "missing required field: " + key}
        return {"status": "ok"}
    '''
)

_HANDLER = textwrap.dedent(
    '''
    REQUIRED = ["folder_name", "owner_email"]


    def handle(payload, context=None):
        for field in REQUIRED:
            if field not in payload:
                return {"status": "error", "error": "missing required field: " + field}
        return {"status": "ok"}
    '''
)


def test_the_live_shape_a_connectors_settings_roster_mines_no_fields():
    assert required_fields_from_rosters(_CONNECTOR) == []
    assert [n for n in handler_required_fields(_CONNECTOR) if n.isupper()] == []


def test_the_spec_the_tester_samples_from_gains_no_settings():
    """The end of the chain that actually failed: TESTER builds its record from
    this spec, so a name that gets in here is a column the test will demand."""
    spec, _changed = align_spec_to_handler_source({"entity": "drive_link", "fields": []}, _CONNECTOR)

    names = [f.get("name") for f in spec.get("fields", []) if isinstance(f, dict)]

    assert not [n for n in names if str(n).isupper()], names


def test_a_real_required_roster_is_still_mined():
    """The guard: the miner was not switched off, it stopped mistaking one
    kind of roster for another."""
    assert sorted(required_fields_from_rosters(_HANDLER)) == ["folder_name", "owner_email"]


def test_a_mixed_roster_keeps_the_fields_and_drops_the_settings():
    source = _HANDLER.replace(
        '["folder_name", "owner_email"]', '["folder_name", "ZZ_API_KEY", "owner_email"]'
    )

    assert sorted(required_fields_from_rosters(source)) == ["folder_name", "owner_email"]


@pytest.mark.parametrize("name", ["ZZ_CLIENT_ID", "ZZ_SECRET", "PORT", "ZZ2_KEY"])
def test_a_constant_shaped_name_is_never_a_field(name):
    assert _usable_align_name(name) is None


@pytest.mark.parametrize("name", ["folder_name", "ownerEmail", "sku", "Id", "vat_rate", "x1"])
def test_anything_not_entirely_upper_case_is_still_a_field(name):
    """Only ALL_CAPS is excluded. camelCase and Capitalised names are left
    alone: a handler may legitimately use them, and the shape says nothing."""
    assert _usable_align_name(name) == name
