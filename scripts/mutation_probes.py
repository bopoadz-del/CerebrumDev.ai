#!/usr/bin/env python3
"""Standalone mutation probes for the zero-artifact false-green.

A verified false-green: the templated/keyless WRITER path could pass CODE
and STORE with ZERO agent-authored artifacts, because (a) no gate counted
authorship at all, (b) the authorship-floor demotion (``below_floor``) fired
only when authorship was MEASURED -- so a build that never measured
authorship sailed through -- and (c) an empty ``cli_authored_ids`` against
an empty blueprint capability set was trivially "set-equal" and handed off
to N3 as if real work had happened.

Each probe below drives the real gate/verdict functions directly against a
zero-artifact input -- no mocked happy path -- and asserts the whole chain
goes RED with a NAMED reason, never a bare boolean. Run standalone:

    python scripts/mutation_probes.py

Exit 0 when every probe correctly detects red. Exit 1 when any probe finds
the chain still passing a zero-artifact build (the mutation escaped).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


class ProbeFailure(AssertionError):
    """A probe's mutation escaped -- the chain stayed green on zero output."""


def probe_writer_gate_refuses_zero_agent_artifacts() -> str:
    """WRITER gate: a pilot-cycle workspace with only templated artifacts
    (zero coding-agent-authored) must fail closed with ``writer_no_output``,
    not silently pass CODE/STORE.
    """
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateContext, gate_writer_contract

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        app_dir = workspace / "app"
        (app_dir / "actions").mkdir(parents=True, exist_ok=True)
        (app_dir / "__init__.py").write_text("", encoding="utf-8")
        (app_dir / "models.py").write_text("MODELS = {}\n", encoding="utf-8")
        docs = workspace / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "build_provenance.json").write_text(
            json.dumps(
                {
                    "artifact_sources": {
                        "widget_intake": "deterministic contract template",
                        "readme": "deterministic contract template",
                    }
                }
            ),
            encoding="utf-8",
        )

        def _runner(argv, *, cwd, timeout):
            raise AssertionError(
                "gate_writer_authorship must not need a subprocess"
            )

        ctx = GateContext(
            workspace=workspace,
            role=BuildRole.WRITER,
            runner=_runner,
            cycle="pilot",
        )
        from app.factory.build.gates import gate_writer_authorship

        result = gate_writer_authorship(ctx)
        if result.ok:
            raise ProbeFailure(
                "gate_writer_authorship passed a pilot-cycle workspace with "
                "zero coding-agent-authored artifacts"
            )
        if "writer_no_output" not in result.detail:
            raise ProbeFailure(
                f"gate_writer_authorship refused for the wrong reason: {result.detail!r}"
            )
    return "gate_writer_authorship: RED with writer_no_output"


def probe_authorship_floor_refuses_unmeasured() -> str:
    """The authorship floor must refuse when authorship was never measured,
    not just when it was measured and came up short.
    """
    from app.factory.build.authorship import full_pilot_authorship_from

    snap = full_pilot_authorship_from({"pilot_ready": True})
    if snap.measured is not False:
        raise ProbeFailure("probe setup invalid: authorship snapshot claims measured")
    if not snap.below_floor:
        raise ProbeFailure(
            "below_floor is False for unmeasured authorship -- a keyless/"
            "templated pilot build with zero authorship signal would reach "
            "Store-green"
        )
    return "full_pilot_authorship_from: below_floor True on unmeasured authorship"


def probe_grade_workspace_refuses_unmeasured_pilot() -> str:
    """grade_workspace must not grade a claimed pilot_ready build FOUNDING /
    STORE_GREEN when authorship was never measured at all.
    """
    from app.factory.build.level_grade import Level, grade_workspace

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        grade = grade_workspace(
            workspace,
            status={
                "state": "succeeded",
                "cycle": "pilot",
                "pilot_ready": True,
                "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
                # No "authorship" key at all: zero-artifact, unmeasured.
            },
        )
        if grade["level"] in {Level.STORE_GREEN.value, Level.FOUNDING_CUSTOMER_READY.value}:
            raise ProbeFailure(
                f"grade_workspace returned {grade['level']} for an unmeasured, "
                "zero-authorship pilot build"
            )
        if grade["pilot_ready"] is not False:
            raise ProbeFailure(
                "grade_workspace kept pilot_ready True for unmeasured authorship"
            )
    return "grade_workspace: unmeasured pilot authorship demoted, pilot_ready False"


def probe_cli_receipt_refuses_empty_blueprint_empty_authored() -> str:
    """cli-pivot: an empty ``cli_authored_ids`` against an empty blueprint
    capability set must not be treated as a (trivially set-equal) pass.
    """
    from app.factory.build.cli_receipt import HANDOFF_TO_N3, ReceiptInvalid, enforce_receipt

    class _EmptyBlueprint:
        capabilities = ()

    try:
        verdict = enforce_receipt(
            blueprint=_EmptyBlueprint(),
            receipt={"schema": "cli_receipt.v1", "cli_authored_ids": []},
            changed_paths=[],
            unified_diff="",
        )
    except ReceiptInvalid as exc:
        if "writer_no_output" not in str(exc):
            raise ProbeFailure(
                f"enforce_receipt refused for the wrong reason: {exc}"
            ) from exc
        return "enforce_receipt: RECEIPT_INVALID (writer_no_output) on empty/empty"
    if verdict.honesty == HANDOFF_TO_N3:
        raise ProbeFailure(
            "enforce_receipt returned HANDOFF_TO_N3 for an empty authored set "
            "against an empty blueprint capability set"
        )
    raise ProbeFailure(f"enforce_receipt returned an unexpected verdict: {verdict}")


PROBES = (
    probe_writer_gate_refuses_zero_agent_artifacts,
    probe_authorship_floor_refuses_unmeasured,
    probe_grade_workspace_refuses_unmeasured_pilot,
    probe_cli_receipt_refuses_empty_blueprint_empty_authored,
)


def run_all() -> int:
    failures = 0
    for probe in PROBES:
        name = probe.__name__
        try:
            detail = probe()
        except ProbeFailure as exc:
            failures += 1
            print(f"[MUTATION ESCAPED] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 -- a probe crash is also a finding
            failures += 1
            print(f"[PROBE ERROR] {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"[RED DETECTED]     {name}: {detail}")
    if failures:
        print(f"\n{failures}/{len(PROBES)} probe(s) did not see the chain go red.")
        return 1
    print(f"\nAll {len(PROBES)} probe(s) confirmed the chain goes red on zero output.")
    return 0


def main() -> int:
    return run_all()


if __name__ == "__main__":
    sys.exit(main())
