"""Straight-through pipeline: CLONER -> WRITER with no pause; TESTER before N3."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.cli_receipt import HANDOFF_TO_N3
from app.factory.build.gates import GateResult, gate_for as real_gate_for
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleError, RoleResult
from app.factory.build.runner import Outcome, RoleRunner, blueprint_hash
from app.factory.build_jobs import build_status

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _bp():
    return load_blueprint(SMOKE)


def _seed_cloner_done(out: Path, bp) -> BuildLedger:
    digest = blueprint_hash(bp)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=digest)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(
            EventKind.GATE_PASSED,
            role=role,
            detail="ok",
            payload={"gate": "seed"},
        )
    return ledger


def _pass_later_gates(role):
    if role in {BuildRole.TESTER, BuildRole.STORE_MANAGER}:
        return lambda _ctx: GateResult(ok=True, gate=f"{role.value}_stub", detail="ok")
    return real_gate_for(role)


def test_cloner_pass_runs_writer_straight_through(tmp_path):
    """A passing CLONER gate must NOT end the run.

    The live loop is COLLECTOR -> CLONER -> WRITER -> TESTER ->
    STORE_MANAGER with no post-Cloner pause and no launch action: WRITER is
    reached on the same run, without a Continue.
    """
    writer_calls: list[bool] = []

    def writer(_ctx):
        writer_calls.append(True)
        raise RoleError("stopped inside WRITER - reached with no hold")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    out = tmp_path / "build"
    outcome = RoleRunner(_bp(), out, roles=roles).run()

    # WRITER ran in the same pass that finished CLONER.
    assert writer_calls == [True]
    assert BuildRole.CLONER in outcome.completed
    # Our stub writer raised; the run is a real role error, never a pause.
    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR

    started = [
        e.role
        for e in BuildLedger(out / "build_ledger.jsonl").events()
        if e.kind is EventKind.PHASE_STARTED
    ]
    assert BuildRole.WRITER in started


def test_build_status_can_never_park_a_run_as_waiting(tmp_path):
    """No code path may report a build as parked after CLONER."""
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = lambda _ctx: RoleResult(ok=True, detail="writer ran")
    out = tmp_path / "build"
    RoleRunner(_bp(), out, roles=roles).run()

    status = build_status(out)
    assert status["state"] != "waiting"
    assert [k for k in status if k.startswith("awaiting_")] == []
    assert "AWAITING" not in str(status.get("outcome") or "")


def test_cli_pivot_writer_runs_tester_before_handoff_to_n3(tmp_path, monkeypatch):
    monkeypatch.setattr("app.factory.build.runner.gate_for", _pass_later_gates)
    order: list[str] = []
    bp = _bp()
    out = tmp_path / "build"
    out.mkdir()
    ledger = _seed_cloner_done(out, bp)

    def handoff_writer(ctx):
        order.append("WRITER")
        payload = {
            "honesty": HANDOFF_TO_N3,
            "next": "n3_gate",
            "green": False,
        }
        ctx.state["cli_pivot"] = payload
        return RoleResult(
            ok=True,
            detail="HANDOFF_TO_N3: receipt clean",
            notes={"cli_pivot": payload, "next": "n3_gate"},
        )

    def tester(_ctx):
        order.append("TESTER")
        return RoleResult(ok=True, detail="acceptance inspector ran")

    def store(_ctx):
        order.append("STORE_MANAGER")
        return RoleResult(ok=True, detail="store registrar ran")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = handoff_writer
    roles[BuildRole.TESTER] = tester
    roles[BuildRole.STORE_MANAGER] = store
    runner = RoleRunner(bp, out, roles=roles, ledger=ledger)
    outcome = runner.run()

    assert order == ["WRITER", "TESTER", "STORE_MANAGER"]
    assert BuildRole.TESTER in outcome.completed
    assert BuildRole.STORE_MANAGER in outcome.completed
    assert outcome.outcome is Outcome.HANDOFF_TO_N3
    assert outcome.ok is False
    assert runner.ledger.succeeded() is False
    terminal = runner.ledger.terminal_event()
    assert terminal is not None
    assert terminal.payload.get("honesty") == HANDOFF_TO_N3
    assert terminal.role is BuildRole.STORE_MANAGER


def test_cli_pivot_tester_fail_rewinds_to_writer(tmp_path, monkeypatch):
    writer_n = [0]
    tester_n = [0]
    bp = _bp()
    out = tmp_path / "build"
    out.mkdir()
    ledger = _seed_cloner_done(out, bp)

    def handoff_writer(ctx):
        writer_n[0] += 1
        payload = {
            "honesty": HANDOFF_TO_N3,
            "next": "n3_gate",
            "green": False,
        }
        ctx.state["cli_pivot"] = payload
        return RoleResult(
            ok=True,
            detail=f"writer pass {writer_n[0]}",
            notes={"cli_pivot": payload, "next": "n3_gate"},
        )

    def tester(_ctx):
        tester_n[0] += 1
        return RoleResult(ok=True, detail=f"tester pass {tester_n[0]}")

    def store(_ctx):
        return RoleResult(ok=True, detail="store")

    def gates(role):
        if role is BuildRole.TESTER:
            def _gate(_ctx):
                if tester_n[0] < 2:
                    return GateResult(
                        ok=False,
                        gate="tester_contract",
                        detail="acceptance red",
                        findings=["analytics_surface missing handle"],
                    )
                return GateResult(ok=True, gate="tester_contract", detail="ok")

            return _gate
        if role is BuildRole.STORE_MANAGER:
            return lambda _ctx: GateResult(ok=True, gate="store_stub", detail="ok")
        return real_gate_for(role)

    monkeypatch.setattr("app.factory.build.runner.gate_for", gates)
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = handoff_writer
    roles[BuildRole.TESTER] = tester
    roles[BuildRole.STORE_MANAGER] = store
    runner = RoleRunner(bp, out, roles=roles, ledger=ledger)
    outcome = runner.run()

    assert tester_n[0] >= 2
    assert writer_n[0] >= 2
    assert outcome.outcome is Outcome.HANDOFF_TO_N3
    assert BuildRole.TESTER in outcome.completed
