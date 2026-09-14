"""Standalone mutation probes for the artifact gate (Phase 0.5, probe P0).

Run directly, no orchestrator, no network:

    python scripts/mutation_probes.py

Every probe imports the REAL factory modules -- ``gate_writer_contract``,
``enforce_receipt``, ``full_pilot_authorship_from`` (``below_floor``) and
``grade_workspace`` -- and calls them. If a probe reimplemented the logic it
would be testing a copy, and a copy proves nothing. Each probe forces zero
agent-authored artifacts and asserts the entire chain goes RED:

    P0a  writer contract gate (CODE) refuses with ``writer_no_output``
    P0b  receipt enforcement refuses an empty-authored empty-required
         handoff (never HANDOFF_TO_N3)
    P0c  unmeasured/zero authorship is below the full-pilot floor
    P0d  level grade refuses Store-green / founding for zero artifacts
    P0e  control: a coding-agent-stamped handler is counted, so the gate's
         refusal in P0a is the check working, not a blanket fail

Exit code is 0 only when every probe passes. RED-when-forced is the probe
suite's whole job: these assertions must hold with the gate live, and the
pytest mutation tests prove they flip when the gate is removed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

# backend root so ``app`` imports when run as ``python scripts/mutation_probes.py``
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.factory.build.authority import BuildRole  # noqa: E402
from app.factory.build.authorship import (  # noqa: E402
    agent_written_handler_ids_in_workspace,
    full_pilot_authorship_from,
)
from app.factory.build.cli_receipt import (  # noqa: E402
    HANDOFF_TO_N3,
    WRITER_NO_OUTPUT,
    ReceiptInvalid,
    enforce_receipt,
)
from app.factory.build.gates import GateContext, gate_writer_contract  # noqa: E402
from app.factory.build.level_grade import Level, grade_workspace  # noqa: E402

Probe = Callable[[], None]


def probe_a_writer_gate_refuses_zero_artifacts() -> None:
    """P0a -- CODE gate: a workspace with no agent artifacts is RED."""
    with tempfile.TemporaryDirectory() as tmp:
        ctx = GateContext(workspace=Path(tmp), role=BuildRole.WRITER)
        result = gate_writer_contract(ctx)
    assert result.ok is False, result.to_json()
    assert WRITER_NO_OUTPUT in result.detail, result.detail
    assert result.gate == "writer_contract"


def probe_b_receipt_refuses_empty_handoff() -> None:
    """P0b -- an empty-authored empty-required receipt never hands to N3."""
    try:
        enforce_receipt(
            blueprint=None,
            blueprint_ids=[],
            receipt={"cli_authored_ids": [], "path_by_id": {}},
            changed_paths=[],
        )
    except ReceiptInvalid as exc:
        message = str(exc)
        assert WRITER_NO_OUTPUT in message, message
    else:
        raise AssertionError(
            f"empty receipt handed off ({HANDOFF_TO_N3}) instead of refusing"
        )


def probe_c_unmeasured_is_below_floor() -> None:
    """P0c -- zero/unmeasured authorship is below the full-pilot floor."""
    floor = full_pilot_authorship_from({}, workspace=None)
    assert floor.measured is False
    assert floor.meets_floor is False
    assert floor.below_floor is True


def probe_d_zero_artifacts_cannot_grade_store_green() -> None:
    """P0d -- STORE/level chain: zero artifacts never grades Store-green."""
    with tempfile.TemporaryDirectory() as tmp:
        grade = grade_workspace(
            tmp,
            status={
                "state": "succeeded",
                "cycle": "pilot",
                "pilot_ready": True,
                "detail": (
                    "CODE PASS \u2014 x; PRODUCT PASS \u2014 y; STORE PASS \u2014 z"
                ),
            },
        )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }, grade
    assert grade["pilot_ready"] is False or not grade["full_pilot"], grade
    assert any("authorship is below" in b for b in grade["blockers"]), grade


def probe_e_control_agent_stamped_handler_is_counted() -> None:
    """P0e -- control: the counter sees coding-agent stamps, so P0a's
    refusal is the check discriminating, not a blanket fail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        actions = root / "app" / "actions"
        actions.mkdir(parents=True)
        (actions / "widget_intake.py").write_text(
            '"""Handler for capability widget_intake.\n\n'
            "Written by the factory WRITER role (coder LLM). Blocks are "
            "invoked through\n"
            "the local dispatch runtime -- this module makes no network "
            'call.\n"""\n',
            encoding="utf-8",
        )
        (actions / "legacy_template.py").write_text(
            '"""Handler for capability legacy_template.\n\n'
            "Written by the factory WRITER role (deterministic contract "
            "template).\n"
            '"""\n',
            encoding="utf-8",
        )
        ids = agent_written_handler_ids_in_workspace(root)
    assert ids == ["widget_intake"], ids


PROBES: List[Tuple[str, Probe]] = [
    ("P0a writer gate refuses zero artifacts", probe_a_writer_gate_refuses_zero_artifacts),
    ("P0b receipt refuses empty handoff", probe_b_receipt_refuses_empty_handoff),
    ("P0c unmeasured is below floor", probe_c_unmeasured_is_below_floor),
    ("P0d zero artifacts cannot grade Store-green", probe_d_zero_artifacts_cannot_grade_store_green),
    ("P0e control: agent stamp is counted", probe_e_control_agent_stamped_handler_is_counted),
]


def main(argv: List[str]) -> int:
    failures = 0
    for name, probe in PROBES:
        try:
            probe()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    print(
        f"mutation_probes: {len(PROBES) - failures}/{len(PROBES)} probes passed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
