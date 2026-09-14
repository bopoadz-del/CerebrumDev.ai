"""Close the zero-artifact false-green.

A verified false-green: the templated/keyless WRITER path passed CODE and
STORE with ZERO agent-authored artifacts. No gate counted authorship, and
the authorship-floor demotion (``below_floor``) fired only when authorship
was MEASURED -- a build that never measured authorship (the templated/
keyless shape) sailed through with no blocker. This file closes it on four
seams and confirms every new refusal names a reason (never a bare boolean):

(a) templated path, zero agent artifacts -> RED (writer_no_output)
(b) cli-pivot path, zero agent artifacts -> RED (via the receipt check)
(c) empty blueprint capability set + empty ``cli_authored_ids`` -> RED
    (writer_no_output), not HANDOFF_TO_N3
(d) unmeasured authorship -> below_floor True / ready False
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.authorship import full_pilot_authorship_from
from app.factory.build.cli_receipt import (
    HANDOFF_TO_N3,
    RECEIPT_INVALID,
    ReceiptInvalid,
    enforce_receipt,
)
from app.factory.build.level_grade import Level, grade_workspace
from app.factory.build.roles import RoleContext, RoleError, run_writer
from app.factory.build.workspace import RoleWorkspace


# -- (a) templated/keyless WRITER path, zero agent artifacts -------------


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = ()


class _Plan:
    def __init__(self, *cids):
        self.capabilities = tuple(_Cap(c) for c in cids)


class _Blueprint:
    product_name = "Zero Artifact Probe"
    product_id = "zero-artifact-probe"
    vertical = "testing"


def _writer_ctx(tmp_path: Path, *, cycle: str, work_list=()) -> RoleContext:
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan("alpha_cap", "beta_cap"),
        work_list=tuple(work_list),
        state={"build_cycle": cycle},
    )


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_templated_writer_zero_agent_artifacts_refuses_pilot_cycle(tmp_path):
    """The templated (keyless) WRITER path must fail loudly, named, on the
    pilot cycle when every capability lands as a template -- zero
    agent-authored artifacts, the shape that used to pass CODE and STORE.
    """
    ctx = _writer_ctx(tmp_path, cycle="pilot")
    with pytest.raises(RoleError, match="writer_no_output"):
        run_writer(ctx)


def test_templated_writer_zero_agent_artifacts_still_allowed_on_code_cycle(tmp_path):
    """The keyless CODE-phase path is a documented, legitimate first-class
    path (CI has no coder key and must still exercise WRITER) -- this guard
    must not regress it. Only the pilot (Store-green) cycle is scored.
    """
    ctx = _writer_ctx(tmp_path, cycle="code")
    result = run_writer(ctx)
    assert result.ok, result.detail


# -- (b) cli-pivot path, zero agent artifacts -----------------------------


def test_cli_pivot_zero_agent_artifacts_does_not_hand_off_to_n3(tmp_path):
    """A non-empty blueprint with an empty authored set must still be the
    ordinary missing-ids refusal (RECEIPT_INVALID), never HANDOFF_TO_N3.
    """

    class _Bp:
        capabilities = [_Cap("analytics_surface"), _Cap("dashboard_surface")]

    with pytest.raises(ReceiptInvalid) as excinfo:
        enforce_receipt(
            blueprint=_Bp(),
            receipt={"schema": "cli_receipt.v1", "cli_authored_ids": []},
            changed_paths=[],
            unified_diff="",
        )
    assert RECEIPT_INVALID in str(excinfo.value)


def test_run_writer_via_cli_pivot_zero_output_raises_role_error(tmp_path, monkeypatch):
    """``run_writer_via_cli_pivot`` must fail closed, not report ok=True,
    when the seam comes back RECEIPT_INVALID instead of HANDOFF_TO_N3.
    """
    from app.factory.build.cli_pivot import ExecutorLaunch, run_writer_via_cli_pivot

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")

    class _Bp:
        product_id = "zero-artifact-probe"
        capabilities = [_Cap("analytics_surface"), _Cap("dashboard_surface")]

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Bp(),
        plan=_Plan("analytics_surface", "dashboard_surface"),
        state={"build_cycle": "pilot"},
    )

    def _launch(**_k):
        return ExecutorLaunch(
            started=True,
            receipt={"schema": "cli_receipt.v1", "cli_authored_ids": []},
            changed_paths=[],
        )

    with pytest.raises(RoleError):
        run_writer_via_cli_pivot(ctx, launch=_launch, env={})


# -- (c) empty blueprint capability set + empty cli_authored_ids ---------


def test_empty_blueprint_empty_authored_refuses_not_handoff():
    """The set-equality receipt check is trivially satisfied when both the
    claimed and required sets are empty. That must not read as a clean
    receipt -- it is zero agent output, not zero required work.
    """

    class _EmptyBlueprint:
        capabilities = ()

    with pytest.raises(ReceiptInvalid) as excinfo:
        verdict = enforce_receipt(
            blueprint=_EmptyBlueprint(),
            receipt={"schema": "cli_receipt.v1", "cli_authored_ids": []},
            changed_paths=[],
            unified_diff="",
        )
        # If enforce_receipt somehow returns instead of raising, this line
        # makes the mutation visible rather than silently passing.
        assert verdict.honesty != HANDOFF_TO_N3  # pragma: no cover
    assert "writer_no_output" in str(excinfo.value)
    assert RECEIPT_INVALID in str(excinfo.value)


# -- (d) unmeasured authorship -> below_floor True / ready False ---------


def test_unmeasured_authorship_is_below_floor():
    snap = full_pilot_authorship_from({})
    assert snap.measured is False
    assert snap.below_floor is True


def test_unmeasured_pilot_build_cannot_grade_store_green(tmp_path):
    """No ``authorship`` key at all -- the templated/keyless shape -- must
    demote a claimed pilot_ready build, not read as Store-green.
    """
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        },
    )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert grade["pilot_ready"] is False


# -- (e) UI co-render: a zero-artifact build cannot grade STORE_GREEN ----
# Frontend display strings live in frontend/src/buildProgress.ts (TS); the
# repo has a vitest runner, so the "0 artifacts" / Store-green co-render
# assertion lives there: frontend/src/__tests__/zeroArtifactFalseGreen.test.ts.
# This backend assertion is the layer that test ultimately depends on: a
# zero-artifact build's level_grade can never be STORE_GREEN, so the
# frontend's ``honestLevel`` has nothing green to read for that build.


def test_zero_artifact_build_level_grade_is_never_store_green(tmp_path):
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {"artifacts": 0, "agent_written": 0, "templated": 0},
        },
    )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }


# -- (6) mutation_probes.py runs standalone and detects red --------------


def test_mutation_probes_script_detects_red():
    """``scripts/mutation_probes.py`` drives the real gate/verdict logic
    (no mocked happy path) and must report every probe RED, exit 0.
    """
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "mutation_probes.py"
    assert script.is_file(), "scripts/mutation_probes.py is missing"
    proc = subprocess.run(
        [_sys.executable, str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RED DETECTED" in proc.stdout
    assert "MUTATION ESCAPED" not in proc.stdout
