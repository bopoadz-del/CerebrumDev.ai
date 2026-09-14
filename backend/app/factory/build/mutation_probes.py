"""Mutation probes — zero-artifact false-greens must turn the chain red.

Standalone, network-free: every probe uses injected executors or plain
file reads, so the suite can run on an air-gapped box and inside CI.

Probes
------

1. ``writer_no_output`` — a workspace whose provenance records zero
   agent-authored artifacts (all ``deterministic contract template``)
   must fail the WRITER gate with ``writer_no_output``. Templated file
   writes never count.
2. ``empty blueprint`` — a clean receipt over an empty blueprint
   capability set must refuse (``RECEIPT_INVALID`` / ``writer_no_output``),
   never hand off to N3.
3. ``unmeasured authorship`` — a run that never recorded authorship data
   is below the launching-ready floor (``below_floor`` is True).
4. ``whole chain`` — a ledger that recorded the WRITER-gate refusal must
   not read back as CODE PASS / STORE PASS / Store-green.

Run: ``python -m app.factory.build.mutation_probes``. Exit 0 when every
probe fails in the direction it must (the mutation is caught); exit 1
otherwise.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, List

from app.factory.build.authority import BuildRole
from app.factory.build.authorship import full_pilot_authorship_from
from app.factory.build.cli_pivot import ExecutorLaunch, run_cli_pivot
from app.factory.build.cli_receipt import (
    HANDOFF_TO_N3,
    RECEIPT_INVALID,
    ReceiptInvalid,
    enforce_receipt,
)
from app.factory.build.gates import GateContext, gate_writer_contract
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.blueprint import CapabilitySpec, FactoryScenario, ProductBlueprint

def probe_empty_blueprint_refuses_handoff() -> None:
    """Empty required set must refuse, never HANDOFF_TO_N3.

    The pydantic blueprint model refuses empty capabilities at intake, but
    the receipt seam must not depend on that: an empty required set (a
    duck-typed blueprint or an explicit empty ``blueprint_ids``) must be
    ``RECEIPT_INVALID`` / ``writer_no_output``, never a clean handoff.
    """
    stub = SimpleNamespace(capabilities=[])
    try:
        enforce_receipt(
            blueprint=stub,
            receipt={"cli_authored_ids": []},
            changed_paths=[],
        )
        caught = False
    except ReceiptInvalid as exc:
        caught = "writer_no_output" in str(exc)
    _check(
        "empty_blueprint_refuses_handoff",
        caught,
        "enforce_receipt did not refuse an empty blueprint capability set",
    )

    blueprint = ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="mutation-empty",
        product_name="Mutation Empty",
        vertical="product",
        summary="One capability, forced-empty required set.",
        capabilities=[CapabilitySpec(id="alpha", description="alpha handler")],
        factory_scenario=FactoryScenario.CREATE_PRODUCT,
    )
    try:
        enforce_receipt(
            blueprint=blueprint,
            blueprint_ids=set(),
            receipt={"cli_authored_ids": []},
            changed_paths=[],
        )
        caught_explicit = False
    except ReceiptInvalid as exc:
        caught_explicit = "writer_no_output" in str(exc)
    _check(
        "empty_required_ids_refuses_handoff",
        caught_explicit,
        "enforce_receipt(blueprint_ids=set()) did not refuse",
    )

    import app.factory.build.cli_receipt as receipt_mod

    saved = receipt_mod.blueprint_capability_set
    receipt_mod.blueprint_capability_set = lambda *_a, **_k: set()
    try:
        with tempfile.TemporaryDirectory() as raw:
            result = run_cli_pivot(
                blueprint,
                Path(raw) / "empty-bp",
                launch=lambda **_k: ExecutorLaunch(
                    started=True,
                    receipt={"cli_authored_ids": []},
                    changed_paths=[],
                ),
            )
    finally:
        receipt_mod.blueprint_capability_set = saved
    _check(
        "empty_blueprint_seam_is_receipt_invalid",
        result.honesty == RECEIPT_INVALID
        and result.honesty != HANDOFF_TO_N3
        and "writer_no_output" in result.detail,
        f"honesty={result.honesty!r} detail={result.detail!r}",
    )

Probe = Callable[[], None]

_FAILURES: List[str] = []


def _check(name: str, condition: bool, detail: str) -> None:
    if condition:
        print(f"PASS  {name}: {detail}")
        return
    _FAILURES.append(name)
    print(f"FAIL  {name}: {detail}")


def _gate_context(workspace: Path) -> GateContext:
    return GateContext(workspace=workspace, role=BuildRole.WRITER)


def probe_zero_agent_artifacts_refuses_writer_gate() -> None:
    """Templated-only provenance must be writer_no_output RED."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        docs = root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "build_provenance.json").write_text(
            json.dumps(
                {
                    "schema_version": "build_provenance.v1",
                    "artifact_sources": {
                        "audit": "deterministic contract template",
                        "workflow": "deterministic contract template",
                    },
                    "brief_dispatch": {},
                }
            ),
            encoding="utf-8",
        )
        result = gate_writer_contract(_gate_context(root))
        _check(
            "zero_agent_artifacts_refuses_writer_gate",
            result.ok is False
            and result.gate == "writer_contract"
            and "writer_no_output" in result.detail,
            f"gate={result.gate} ok={result.ok} detail={result.detail!r}",
        )


def probe_unmeasured_authorship_is_below_floor() -> None:
    """Unmeasured authorship is a refusal, not a silent pass."""
    snap = full_pilot_authorship_from({"pilot_ready": True})
    _check(
        "unmeasured_authorship_is_below_floor",
        snap.measured is False
        and snap.meets_floor is False
        and snap.below_floor is True,
        (
            f"measured={snap.measured} meets_floor={snap.meets_floor} "
            f"below_floor={snap.below_floor}"
        ),
    )


def probe_whole_chain_goes_red() -> None:
    """A writer_no_output refusal must not read back as CODE/STORE PASS."""
    from app.factory.build_jobs import build_status

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        ledger = BuildLedger(root / "build_ledger.jsonl")
        ledger.start_run(product_id="mutation-chain", inputs_hash="mutation")
        ledger.append(
            EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER"
        )
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.WRITER,
            detail=(
                "writer_no_output: zero agent-authored artifacts — "
                "23 file(s) written, all templated"
            ),
            payload={"gate": "writer_contract", "findings": []},
        )
        ledger.append(
            EventKind.RUN_FAILED,
            role=BuildRole.WRITER,
            detail=(
                "WRITER gate 'writer_contract' failed: writer_no_output: "
                "zero agent-authored artifacts"
            ),
            payload={"outcome": "FAILED_GATE"},
        )
        status = build_status(root)
        detail = str(status.get("detail") or "")
        level = (
            status.get("level_grade") or {}
        ).get("level")
        _check(
            "whole_chain_goes_red",
            "CODE PASS" not in detail
            and "STORE PASS" not in detail
            and level != "STORE_GREEN",
            f"detail={detail!r} level={level!r}",
        )


PROBES: List[Probe] = [
    probe_zero_agent_artifacts_refuses_writer_gate,
    probe_empty_blueprint_refuses_handoff,
    probe_unmeasured_authorship_is_below_floor,
    probe_whole_chain_goes_red,
]


def main() -> int:
    for probe in PROBES:
        try:
            probe()
        except Exception as exc:  # noqa: BLE001 — a probe crash is a failure
            _FAILURES.append(probe.__name__)
            print(f"FAIL  {probe.__name__}: crashed: {type(exc).__name__}: {exc}")
    if _FAILURES:
        print(f"{len(_FAILURES)} probe(s) FAILED: {', '.join(_FAILURES)}")
        return 1
    print(f"ALL {len(PROBES)} MUTATION PROBES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
