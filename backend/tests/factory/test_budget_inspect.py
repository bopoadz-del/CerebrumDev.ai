"""Stop-and-inspect: 30 min hard-stop, inspect, optional 45 min — never silent 2h."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.budget_inspect import (
    CEILING_S,
    STAGE_1_S,
    STAGE_2_S,
    inspect_build,
    inspect_decision,
    next_stage_wall,
    should_continue_after_inspect,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build_jobs import build_status


def _ledger(tmp_path: Path) -> BuildLedger:
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vetconnect", inputs_hash="abc")
    return ledger


def test_inspect_reads_caps_timeouts_stubs_and_contract_misses(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler site_visits (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "site_visits",
            "source": "coder LLM (kimi)",
            "done": 1,
            "total": 4,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler billing (deterministic contract template)",
        payload={
            "stage": "handlers",
            "capability": "billing",
            "source": "deterministic contract template",
            "done": 2,
            "total": 4,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="coder LLM timed out writing handler inventory",
        payload={"stage": "coder", "capability": "inventory", "model_call": True},
    )
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail="unknown field(s): action",
        payload={"findings": ["billing puts 'action' inside the execute() payload"]},
    )
    snap = inspect_build(ledger, tmp_path / "build")
    assert snap["current_capability"] == "inventory"
    assert "site_visits" in snap["caps_written"]
    assert "billing" in snap["caps_templated"]
    assert snap["agent_written"] == 1
    assert snap["templated"] >= 1
    assert snap["stub_rate"] > 0
    assert any("timed out" in t for t in snap["timeouts"])
    assert any("action" in m for m in snap["contract_misses"])
    assert snap["pilot_ready"] is False
    assert any("pilot_ready is false" in b for b in snap["pilot_ready_blockers"])
    assert snap["progressing"] is True


def test_all_stubs_and_timeouts_are_not_progressing(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler billing (deterministic contract template)",
        payload={
            "stage": "handlers",
            "capability": "billing",
            "source": "deterministic contract template",
        },
    )
    ledger.append(
        EventKind.NOTE,
        detail="coder LLM timed out after 2400s",
        payload={"model_call": True, "capability": "inventory"},
    )
    snap = inspect_build(ledger)
    assert snap["progressing"] is False
    assert should_continue_after_inspect(snap) is False
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "hard_stop"
    assert decided["next_wall_s"] is None
    assert decided["continue"] is False
    assert "hard-stop" in decided["reason"]


def test_progressing_inspect_bumps_only_to_45_minutes(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler site_visits (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "site_visits",
            "source": "coder LLM (kimi)",
            "done": 3,
            "total": 6,
        },
    )
    snap = inspect_build(ledger)
    assert should_continue_after_inspect(snap) is True
    assert next_stage_wall(STAGE_1_S, STAGE_1_S, snap) == STAGE_2_S
    assert next_stage_wall(STAGE_2_S, STAGE_2_S, snap) is None
    assert next_stage_wall(STAGE_1_S, CEILING_S, snap) is None
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "continue_stage_2"
    assert decided["next_wall_s"] == STAGE_2_S
    assert CEILING_S not in (decided["next_wall_s"], decided["current_wall_s"])


def test_high_leftover_wall_is_inspected_not_cut(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler site_visits (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "site_visits",
            "source": "coder LLM (kimi)",
        },
    )
    snap = inspect_build(ledger)
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=CEILING_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "inspect_only_high_wall_honored"
    assert decided["next_wall_s"] is None
    assert decided["current_wall_s"] == CEILING_S


def test_runner_hard_stops_at_stage_1_without_progress(tmp_path):
    """A spent 30 min stage with only stubs must not bump to 2h or succeed."""
    now = {"t": 0.0}

    def clock():
        return now["t"]

    def hanging(ctx):
        ctx.note(
            "wrote handler billing (deterministic contract template)",
            stage="handlers",
            capability="billing",
            source="deterministic contract template",
            done=1,
            total=1,
        )
        now["t"] = STAGE_1_S + 5
        ctx.note(
            "stage wall reached — inspect",
            stage="budget",
            capability="billing",
            source="deterministic contract template",
        )
        from app.factory.build.roles_models import RoleResult

        return RoleResult(ok=True, detail="templated")

    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = hanging
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1, wall_clock_s=STAGE_1_S, phase_wall_clock_s=STAGE_1_S
        ),
        clock=clock,
        auto_pilot=True,
    )
    outcome = runner.run()
    assert outcome.ok is False
    assert outcome.outcome.value == "FAILED_BUDGET_SPENT"
    inspects = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("budget_inspect")
    ]
    assert inspects, "stage stop must emit an inspect snapshot"
    snap = inspects[-1].payload
    assert snap["decision"] == "hard_stop"
    assert snap.get("next_wall_s") is None
    assert runner.budget.wall_clock_s == STAGE_1_S
    assert runner.ledger.pilot_ready() is False
    status = build_status(tmp_path / "build")
    assert status["pilot_ready"] is False
    assert status["budget_inspect"]["decision"] == "hard_stop"
    assert "caps_written" in status["budget_inspect"]
    assert "stub_rate" in status["budget_inspect"]


def test_runner_ramps_to_45m_only_when_inspect_sees_agent_work(tmp_path):
    now = {"t": 0.0}

    def clock():
        return now["t"]

    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.roles_models import RoleResult

    def writing(ctx):
        ctx.note(
            "wrote handler site_visits (coder LLM (kimi))",
            stage="handlers",
            capability="site_visits",
            source="coder LLM (kimi)",
            done=1,
            total=2,
        )
        now["t"] = STAGE_1_S + 1
        ctx.note(
            "wrote handler intake (coder LLM (kimi))",
            stage="handlers",
            capability="intake",
            source="coder LLM (kimi)",
            done=2,
            total=2,
        )
        return RoleResult(ok=True, detail="agent wrote")

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writing
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1, wall_clock_s=STAGE_1_S, phase_wall_clock_s=5400
        ),
        clock=clock,
        auto_pilot=True,
    )
    outcome = runner.run()
    inspects = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("budget_inspect")
    ]
    assert inspects
    decisions = [e.payload.get("decision") for e in inspects]
    assert "continue_stage_2" in decisions
    assert CEILING_S not in decisions
    assert runner.budget.wall_clock_s == STAGE_2_S
    assert runner.budget.wall_clock_s != CEILING_S
    # Replaced WRITER may fail later gates; the contract is the staged ramp.
    assert runner.ledger.pilot_ready() is False
    assert outcome.outcome is not None
