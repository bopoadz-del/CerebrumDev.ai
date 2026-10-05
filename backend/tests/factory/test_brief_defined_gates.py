"""A gate exists only if a brief can turn it on (AGENTS.md, GATES.md).

A round's failures are classified brief-defined vs factory-invented BEFORE
any writer dispatch. A failure on a check the brief never defined is the
gate's fault, not the product's: it goes advisory with the reason in the
ledger, and no writer round is spent on it.

The invented gate id below is made up for this file. It is in no floor, no
brief and no gate, which is exactly the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import brief_gates
from app.factory.build import runner as runner_mod
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

INVENTED = "zorblat_check"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def _events(runner, kind):
    return [e for e in runner.ledger.events() if e.kind is kind]


def _tester_gate_failing_once(monkeypatch, verdict):
    """The real gates, except the first TESTER verdict is ``verdict``."""
    real = runner_mod.gate_for
    calls = {"tester": 0}

    def gate_for(role):
        gate = real(role)
        if BuildRole(role) is not BuildRole.TESTER:
            return gate

        def tester(ctx):
            calls["tester"] += 1
            if calls["tester"] == 1:
                return verdict
            return gate(ctx)

        return tester

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    return calls


def _recording_writer(captured):
    real = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        captured.append(tuple(ctx.work_list))
        return real(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    return roles


# -- the classification itself ----------------------------------------------


def test_the_brief_defines_its_acceptance_checks_and_not_an_invented_one(blueprint):
    from app.factory.blocks_source import resolve_blocks_root
    from app.factory.planner import CapabilityPlanner

    root = resolve_blocks_root()
    root = Path(root) if root else None
    accept = brief_gates.compiled_acceptance_checks(
        blueprint, CapabilityPlanner(root).plan(blueprint), blocks_root=root
    )
    defined = brief_gates.brief_defined_checks(blueprint, accept)

    # the checks the TESTER verdicts declare are ones the compiled brief turns on
    assert brief_gates.SUITE_CHECK in defined
    assert brief_gates.PRODUCT_GATE_CHECK in defined
    # and so are the floor checks this brief enforces
    from app.factory.build.acceptance_floor import enforced_ids

    assert set(enforced_ids(blueprint)) <= defined
    assert INVENTED not in defined


def test_split_is_by_declared_id_never_by_finding_text():
    verdict = GateResult(
        ok=False,
        gate="suite_green",
        # the finding TEXT names a defined check; the declared id does not
        findings=["gates gates gates"],
        payload={"check": INVENTED},
    )
    split = brief_gates.split_failures(verdict, frozenset({brief_gates.SUITE_CHECK}))

    assert split.defined == ()
    assert split.invented_checks == [INVENTED]


# -- the runner ---------------------------------------------------------------


def test_an_invented_gate_goes_advisory_with_zero_writer_rounds(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _tester_gate_failing_once(
        monkeypatch,
        GateResult(
            ok=False,
            gate=INVENTED,
            reason="invented_failed",
            detail="a check no brief asked for",
            findings=["the factory's own idea of done"],
            payload={"check": INVENTED},
        ),
    )
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 0
    assert _events(runner, EventKind.REWORK) == [], "no writer round was spent"
    assert len(captured) == 1, "the writer ran once -- its first pass -- and never again"

    events = list(runner.ledger.events())
    advisory = next(
        i for i, e in enumerate(events)
        if e.kind is EventKind.NOTE and (e.payload or {}).get("gate_advisory")
    )
    record = events[advisory].payload
    assert record["gate_advisory"][0]["check"] == INVENTED
    assert record["gate_advisory"][0]["reason"] == brief_gates.REASON_NOT_DEFINED
    assert record["writer_dispatched"] is False
    assert not [
        e for e in events[advisory:]
        if e.kind is EventKind.PHASE_STARTED and e.role is BuildRole.WRITER
    ], "zero WRITER phases after the advisory"
    passed = next(
        e for e in events[advisory:]
        if e.kind is EventKind.GATE_PASSED and e.role is BuildRole.TESTER
    )
    assert passed.payload["advisory"] is True
    assert passed.payload["advisory_checks"] == [INVENTED]


def test_a_brief_defined_failure_still_goes_to_the_writer(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _tester_gate_failing_once(
        monkeypatch,
        GateResult(
            ok=False,
            gate="suite_green",
            reason="suite_red",
            detail="suite is red",
            findings=["FAILED tests/test_models.py::test_round_trip - boom"],
            payload={"check": brief_gates.SUITE_CHECK},
        ),
    )
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 1
    assert len(_events(runner, EventKind.REWORK)) == 1
    assert captured[-1] == ("FAILED tests/test_models.py::test_round_trip - boom",)
    assert not [
        e for e in _events(runner, EventKind.NOTE) if (e.payload or {}).get("gate_advisory")
    ]


def test_mixed_round_hands_the_writer_only_the_brief_defined_failure(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _tester_gate_failing_once(
        monkeypatch,
        GateResult(
            ok=False,
            gate="suite_green",
            reason="suite_red",
            detail="two failures",
            findings=["invented finding", "defined finding"],
            payload={"finding_checks": [INVENTED, brief_gates.SUITE_CHECK]},
        ),
    )
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 1
    assert captured[-1] == ("defined finding",)
    rework = _events(runner, EventKind.REWORK)[0]
    assert rework.payload["findings"] == ["defined finding"]
    note = next(
        e for e in _events(runner, EventKind.NOTE) if (e.payload or {}).get("gate_advisory")
    )
    assert [a["check"] for a in note.payload["gate_advisory"]] == [INVENTED]
    assert note.payload["gate_advisory"][0]["findings"] == ["invented finding"]
    assert note.payload["writer_dispatched"] is True


def test_the_real_tester_verdict_declares_its_brief_check(tmp_path):
    from app.factory.build.gates import GateContext, gate_tester_contract

    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "app").mkdir()
    (ws / "app" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "tests" / "test_one.py").write_text("def test_x():\n    assert 1 == 2\n", encoding="utf-8")

    verdict = gate_tester_contract(GateContext(workspace=ws, role=BuildRole.TESTER))

    assert not verdict.ok
    assert verdict.payload["check"] == brief_gates.SUITE_CHECK


def test_the_old_standing_order_is_gone_and_the_new_one_is_written():
    old = "update GATES.md with any failure a gate missed"
    new = "A gate exists only if a brief can turn it on."
    for rel in ("AGENTS.md", "GATES.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert old not in text, rel
        assert new in " ".join(text.split()), rel
    for path in (ROOT / "prompts").glob("*.md"):
        assert old not in path.read_text(encoding="utf-8"), path.name
