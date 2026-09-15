"""CHADi lock 2026-09-14: Writer hold after Cloner; TESTER before N3."""

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
from app.factory.build.writer_control import (
    AWAITING_MR_FINANCE_WRITER,
    writer_requires_handoff,
)
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


def test_writer_requires_handoff_explicit_and_unset(monkeypatch):
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "1")
    assert writer_requires_handoff() is True
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "0")
    assert writer_requires_handoff() is False
    monkeypatch.delenv("FACTORY_WRITER_REQUIRES_HANDOFF", raising=False)
    # Unset runs the full pipeline straight through - the MR. FINANCE hold
    # is opt-in legacy, production included.
    assert writer_requires_handoff() is False
    assert writer_requires_handoff({"ENV": "prod"}) is False
    assert writer_requires_handoff({"ENV": "test"}) is False
    assert writer_requires_handoff({"FACTORY_WRITER_REQUIRES_HANDOFF": "1"}) is True


def test_post_cloner_hold_does_not_launch_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "1")
    writer_calls: list[bool] = []

    def boom_writer(_ctx):
        writer_calls.append(True)
        raise AssertionError("WRITER / BA must not auto-start after Cloner")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = boom_writer
    runner = RoleRunner(_bp(), tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert writer_calls == []
    assert outcome.outcome is Outcome.AWAITING_MR_FINANCE_WRITER
    assert outcome.ok is False
    assert BuildRole.WRITER not in outcome.completed
    assert BuildRole.CLONER in outcome.completed
    status = build_status(tmp_path / "build")
    assert status["state"] == "waiting"
    assert status["honesty"] == AWAITING_MR_FINANCE_WRITER
    assert status["awaiting_mr_finance_writer"] is True
    assert status["next"] == "writer"


def test_continue_after_hold_launches_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "1")
    writer_calls: list[bool] = []

    def writer(_ctx):
        writer_calls.append(True)
        raise RoleError("stopped after proving MR. FINANCE launched Writer")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    out = tmp_path / "build"
    first = RoleRunner(_bp(), out, roles=roles)
    hold = first.run()
    assert hold.outcome is Outcome.AWAITING_MR_FINANCE_WRITER
    assert writer_calls == []

    second = RoleRunner(_bp(), out, roles=roles, ledger=first.ledger)
    launched = second.run()
    assert writer_calls == [True]
    assert launched.outcome is Outcome.FAILED_ROLE_ERROR


def test_cli_pivot_writer_runs_tester_before_handoff_to_n3(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "0")
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
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "0")
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


def test_awaiting_is_not_terminal_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_WRITER_REQUIRES_HANDOFF", "1")
    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = lambda _ctx: RoleResult(ok=True, detail="no")
    runner = RoleRunner(_bp(), out, roles=roles)
    runner.run()

    from types import SimpleNamespace

    from app.factory import platform_chat_flow

    state = SimpleNamespace(
        session_id="sess_hold",
        product_design=SimpleNamespace(
            blueprint={"product_id": "runner-smoke"},
            generation={"engine": "runner", "output_dir": str(out)},
        ),
    )
    assert platform_chat_flow.is_awaiting_mr_finance_writer(state) is True
    assert platform_chat_flow.is_generation_terminal_failure(state) is False
    assert platform_chat_flow.is_generation_resumable(state) is True
    assert platform_chat_flow.has_running_build(state) is False
