"""A rework pass is a ratchet: it may only touch what failed.

New-shape tests for the whack-a-mole defect of the seventh live build. Every
rework round regenerated every handler, spec and route; the coder is
nondeterministic, so round 4 fixed defect_register and REGRESSED
site_inspection_log, and the budget ran out with the suite still red. The
WRITER now regenerates only the capabilities the findings implicate and
reuses everything else from the previous round.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import (
    RoleContext,
    _failing_capability_ids,
    run_writer,
)
from app.factory.build.workspace import RoleWorkspace


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_no_findings_means_everything_is_fair_game():
    assert _failing_capability_ids((), ["a", "b"]) == {"a", "b"}


def test_named_findings_implicate_only_their_capabilities():
    findings = [
        "E  AssertionError: site_inspection_log rejected a payload built "
        "from its own schema: Missing required fields",
    ]
    caps = ["site_inspection_log", "defect_register", "crew_assignment"]
    assert _failing_capability_ids(findings, caps) == {"site_inspection_log"}


def test_findings_naming_nothing_regenerate_everything():
    """An infrastructure failure cannot be localised; guessing 'nothing'
    would end the rework with the suite still red."""
    findings = ["E  ImportError: cannot import name 'app'"]
    assert _failing_capability_ids(findings, ["a", "b"]) == {"a", "b"}


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = ()


class _Plan:
    def __init__(self, *cids):
        self.capabilities = tuple(_Cap(c) for c in cids)


class _Blueprint:
    product_name = "Ratchet Probe"
    product_id = "ratchet-probe"
    vertical = "testing"


def _writer_ctx(tmp_path: Path, work_list=(), state=None):
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan("alpha_cap", "beta_cap"),
        work_list=tuple(work_list),
        state=dict(state or {}),
    )


def test_a_green_capability_survives_a_rework_round_untouched(tmp_path):
    """The handler file of a capability the findings do not implicate must
    not be rewritten -- byte-for-byte, sentinel included."""
    first = _writer_ctx(tmp_path)
    result = run_writer(first)
    assert result.ok

    state = dict(first.state)
    state.update(result.notes)

    # Plant a sentinel in the passing capability's handler; a regeneration
    # would erase it.
    handler = tmp_path / "build" / "app" / "actions" / "alpha_cap.py"
    sentinel = handler.read_text(encoding="utf-8") + "# SENTINEL: round-1 body\n"
    handler.write_text(sentinel, encoding="utf-8")

    rework = _writer_ctx(
        tmp_path,
        work_list=["E  AssertionError: beta_cap rejected a payload"],
        state=state,
    )
    result = run_writer(rework)
    assert result.ok

    assert handler.read_text(encoding="utf-8") == sentinel, (
        "a rework round that implicated only beta_cap rewrote alpha_cap"
    )
    assert result.notes["artifact_sources"]["alpha_cap"] in (
        "deterministic contract template",
        "unchanged from previous round",
    )


def test_a_failing_capability_is_regenerated(tmp_path):
    first = _writer_ctx(tmp_path)
    result = run_writer(first)
    state = dict(first.state)
    state.update(result.notes)

    handler = tmp_path / "build" / "app" / "actions" / "beta_cap.py"
    poisoned = handler.read_text(encoding="utf-8") + "# SENTINEL: broken body\n"
    handler.write_text(poisoned, encoding="utf-8")

    rework = _writer_ctx(
        tmp_path,
        work_list=["E  AssertionError: beta_cap rejected a payload"],
        state=state,
    )
    run_writer(rework)
    assert "SENTINEL" not in handler.read_text(encoding="utf-8"), (
        "the failing capability's handler was not regenerated"
    )


def test_specs_of_green_capabilities_are_reused(tmp_path):
    """Schema stability across rounds: a green capability's entity and fields
    must not drift while another capability is being fixed -- drifting
    schemas were half of every 'no column named X' failure."""
    first = _writer_ctx(tmp_path)
    result = run_writer(first)
    state = dict(first.state)
    state.update(result.notes)
    frozen = state["model_specs"]["alpha_cap"]

    rework = _writer_ctx(
        tmp_path,
        work_list=["E  beta_cap exploded"],
        state=state,
    )
    reworked = run_writer(rework)
    assert reworked.notes["model_specs"]["alpha_cap"] == frozen
