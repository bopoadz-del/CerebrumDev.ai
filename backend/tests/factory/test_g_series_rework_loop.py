"""G-series: the rework loop dies at the root.

Live: one voice-agent build was resumed four times; every resume rebuilt a
fresh workspace from COLLECTOR, and every TESTER failure -- several of them in
tests the Factory itself generated, which the WRITER is forbidden to edit --
sent the writer back for another full pass until the budget ran out.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import failure_owner
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext, gate_suite_green
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import RoleRunner
from app.factory.build_jobs import reattach_point

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def _tester_with_a_broken_generated_test(ctx):
    """The real TESTER, plus one deliberately broken test it writes itself."""
    result = ROLE_IMPLEMENTATIONS[BuildRole.TESTER](ctx)
    ctx.workspace.write_text(
        Path("tests") / "test_zz_broken_generated.py",
        "def test_zz_broken_generated():\n    assert False, 'broken on purpose'\n",
    )
    return result


def _events(runner, kind):
    return [e for e in runner.ledger.events() if e.kind is kind]


# ── G1 acceptance ──────────────────────────────────────────────────────────


def test_a_broken_generated_test_halts_as_factory_fault_with_zero_rework(
    blueprint, tmp_path, stub_coder
):
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = _tester_with_a_broken_generated_test

    runner = RoleRunner(blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert not outcome.ok
    assert outcome.rework_used == 0
    assert "FACTORY_FAULT" in outcome.detail
    assert "test_zz_broken_generated" in outcome.detail
    assert _events(runner, EventKind.REWORK) == [], "no writer was dispatched"
    note = next(e for e in _events(runner, EventKind.NOTE) if "FACTORY_FAULT" in (e.detail or ""))
    assert note.payload["rework"] == 0
    assert note.payload["writer_dispatched"] is False
    assert note.payload["generator"], "the ledger names where the test was generated"


def test_a_product_code_failure_still_goes_to_the_writer():
    """The guard: a Factory-written test whose traceback ends in app/** is the
    product failing, which the writer CAN fix."""
    verdict = SimpleNamespace(
        reason="suite_red",
        gate="suite_green",
        findings=[],
        payload={"failing_tests": [{
            "file": "tests/test_models.py", "name": "test_every_model_round_trips",
            "nodeid": "tests/test_models.py::test_every_model_round_trips",
            "innermost": "app/store.py",
        }]},
    )

    owned = failure_owner.classify(verdict, ["tests/test_models.py"])

    assert owned["owner"] == failure_owner.PRODUCT


def test_a_writer_authored_test_is_the_writers_to_fix():
    verdict = SimpleNamespace(
        reason="suite_red", gate="suite_green", findings=[],
        payload={"failing_tests": [{"file": "tests/test_callops.py", "name": "t",
                                    "nodeid": "tests/test_callops.py::t", "innermost": "tests/test_callops.py"}]},
    )

    assert failure_owner.classify(verdict, ["tests/test_models.py"])["owner"] == failure_owner.PRODUCT


# ── G2 acceptance ──────────────────────────────────────────────────────────


def test_a_run_killed_at_tester_is_re_entered_at_tester(blueprint, tmp_path, stub_coder):
    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = _tester_with_a_broken_generated_test
    first = RoleRunner(blueprint, out, roles=roles)
    assert not first.run().ok
    seen = len(list(first.ledger.events()))

    assert reattach_point(out) == ("TESTER", "")

    (out / "tests" / "test_zz_broken_generated.py").unlink()
    second = RoleRunner(blueprint, out)
    outcome = second.run()

    after = list(second.ledger.events())[seen:]
    started = {e.role for e in after if e.kind is EventKind.PHASE_STARTED}
    assert BuildRole.TESTER in started
    assert not started & {BuildRole.COLLECTOR, BuildRole.CLONER, BuildRole.WRITER}, started
    assert outcome.ok, outcome.detail


def test_a_missing_workspace_is_not_re_entered(tmp_path):
    phase, why = reattach_point(tmp_path / "nothing-here")

    assert phase is None and why


def test_start_runner_build_records_resumed_on_the_same_workspace(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    from app.factory import build_jobs

    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = _tester_with_a_broken_generated_test
    assert not RoleRunner(blueprint, out, roles=roles).run().ok

    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready", lambda *a, **k: None
    )
    started = []
    monkeypatch.setattr(
        build_jobs.threading, "Thread",
        lambda *a, **k: SimpleNamespace(start=lambda: started.append(True), name="t"),
    )

    result = build_jobs.start_runner_build(blueprint, out)

    assert Path(result["output_dir"]) == out, "same workspace, not a __runN sibling"
    from app.factory.build.ledger import BuildLedger

    notes = [e.detail for e in BuildLedger(build_jobs._ledger_path(out)).events()
             if e.kind is EventKind.NOTE]
    assert f"RESUMED workspace={out.name} phase=TESTER" in notes


def test_the_rework_counter_survives_a_resume(blueprint, tmp_path, stub_coder):
    out = tmp_path / "build"
    runner = RoleRunner(blueprint, out)
    runner.ledger.start_run(product_id=blueprint.product_id, inputs_hash="x")
    runner.ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="round 1",
                         payload={"failure_names": ["a"]})
    runner.ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="round 2",
                         payload={"failure_names": ["b"]})

    assert runner._rework_rounds_this_cycle() == 2
    assert runner._last_rework_failures() == ["b"]


# ── G4 acceptance ──────────────────────────────────────────────────────────


def _workspace(tmp_path, test_body):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "app").mkdir()
    (ws / "app" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "tests" / "test_one.py").write_text(test_body, encoding="utf-8")
    return ws


def _gate(ws):
    def run(argv, *, cwd, timeout):
        return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                              env={**__import__("os").environ, "FORCE_COLOR": "1", "PY_COLORS": "1"})

    return gate_suite_green(GateContext(workspace=ws, role=BuildRole.TESTER, runner=run))


def test_a_genuinely_failing_test_is_suite_red_with_its_name(tmp_path):
    """Colour forced ON: the gate must still read the result, not the paint."""
    result = _gate(_workspace(tmp_path, "def test_real():\n    assert 1 == 2\n"))

    assert result.reason == "suite_red"
    assert any("tests/test_one.py::test_real" in f for f in result.findings), result.findings
    assert result.payload["failing_tests"][0]["name"] == "test_real"


def test_a_missing_dependency_is_an_environment_fault(tmp_path):
    result = _gate(_workspace(tmp_path, "import zz_no_such_dependency_anywhere\n\n\ndef test_x():\n    pass\n"))

    assert result.reason == "environment_fault", (result.reason, result.detail)
    assert "zz_no_such_dependency_anywhere" in result.detail


def test_a_passing_suite_is_green(tmp_path):
    assert _gate(_workspace(tmp_path, "def test_ok():\n    assert True\n")).ok


# ── G5 acceptance ──────────────────────────────────────────────────────────


def test_the_same_failure_twice_is_named_and_stops():
    assert failure_owner.repeated(["tests/a.py::t1", "tests/a.py::t2"], ["tests/a.py::t2"]) == ["tests/a.py::t2"]
    assert failure_owner.repeated(["tests/a.py::t1"], ["tests/a.py::t3"]) == []
    assert failure_owner.repeated([], ["x"]) == []


def test_g5_is_in_the_runner_not_the_prompt():
    import inspect

    from app.factory.build import runner

    assert "SAME_FAILURE_TWICE" in inspect.getsource(runner)
    assert "SAME_FAILURE_TWICE" not in (ROOT / "backend/app/factory/build/writer_prompt.py").read_text(
        encoding="utf-8"
    )

