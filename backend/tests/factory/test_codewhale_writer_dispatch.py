"""Phase 5 dispatch wiring: the CodeWhale worker as the WRITER target."""

from __future__ import annotations

import pytest

from app.factory.build.codewhale_worker import WorkerError
from app.factory.build.roles_handlers import (
    _run_writer_via_codewhale_worker,
    writer_uses_codewhale,
)
from app.factory.build.roles_models import RoleError
from app.factory.build.writer_prompt import render_writer_prompt


def test_writer_uses_codewhale_is_opt_in():
    assert writer_uses_codewhale({}) is False
    assert writer_uses_codewhale({"FACTORY_CODEWHALE_WRITER": "0"}) is False
    assert writer_uses_codewhale({"FACTORY_CODEWHALE_WRITER": "1"}) is True


def test_prompt_instructs_the_author_stamp():
    text = render_writer_prompt(
        type("B", (), {"product_id": "p", "product_name": "n", "vertical": "v", "summary": "s"})(),
        brief="probe",
    )
    assert "Written by the factory WRITER role (codewhale exec)" in text


def _ctx(tmp_path):
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=type(
            "B",
            (),
            {"product_id": "p", "product_name": "n", "vertical": "v", "summary": "s"},
        )(),
        plan=None,
        state={},
    )


def _receipt(tools, **kw):
    base = {
        "status": "completed",
        "termination_reason": "resolved",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "output": "built",
        "tools": tools,
        "error": None,
        "error_category": None,
    }
    base.update(kw)
    base["to_dict"] = lambda self: {
        k: v
        for k, v in type(self).__dict__.items()
        if k != "to_dict" and not k.startswith("__")
    }
    return type("R", (), base)()


def _plant_authored_handler(root):
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / "cap.py").write_text(
        '"""Handler for capability cap.\n\n'
        'Written by the factory WRITER role (codewhale exec).\n'
        '"""\n',
        encoding="utf-8",
    )


def _ctx_with_budget(tmp_path, seconds_left):
    """A writer ctx whose phase budget has ``seconds_left`` remaining."""
    import time as _time

    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")
    now = _time.monotonic()
    ctx.deadline_box = {"at": now + float(seconds_left), "clock": _time.monotonic}
    return ctx


def test_writer_worker_gets_the_phase_budget_not_a_flat_wall(tmp_path, monkeypatch):
    """D1: the WRITER was dispatched with no timeout_s, so worker_timeout_s()
    returned the flat 1800s DEFAULT and killed the subprocess 30 min in --
    even on a pilot run granted a 90-minute phase wall (live 2026-09-29:
    'wall-clock budget of 1800s spent before WRITER completed, written=0').
    A pilot-budget writer must be dispatched with a wall that tracks the
    phase budget and stays above 1800s, so it can still be IN FLIGHT when
    the budget inspector looks at the stage-1 boundary."""
    from app.factory.build.budget_inspect import STAGE_1_S

    captured = {}

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        captured["timeout_s"] = timeout_s
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    ctx = _ctx_with_budget(tmp_path, seconds_left=5400.0)  # pilot phase wall
    assert _run_writer_via_codewhale_worker(ctx).ok is True

    wall = captured["timeout_s"]
    assert wall is not None, "writer dispatched with no timeout_s (the D1 bug)"
    assert wall > STAGE_1_S, (
        f"writer wall {wall}s <= stage-1 boundary {STAGE_1_S}s: the worker is "
        "killed before the budget inspector's in-flight bump can fire"
    )
    assert wall <= 5400.0 + 1, "worker must die before the phase wall, not after"


def test_code_only_writer_budget_keeps_the_1800s_floor(tmp_path, monkeypatch):
    """A code-only pass (short phase wall) must not be granted LESS than the
    historical 1800s default, and must not be silently extended either."""
    from app.factory.build.codewhale_worker import DEFAULT_WORKER_TIMEOUT_S

    captured = {}

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        captured["timeout_s"] = timeout_s
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    ctx = _ctx_with_budget(tmp_path, seconds_left=1500.0)  # code-only phase wall
    assert _run_writer_via_codewhale_worker(ctx).ok is True
    assert captured["timeout_s"] == DEFAULT_WORKER_TIMEOUT_S


def test_unbounded_budget_falls_back_to_the_worker_default(tmp_path, monkeypatch):
    """No deadline set (tests, unbounded runs): pass None and let
    run_worker_job apply its own default rather than inventing a wall."""
    captured = {"timeout_s": "unset"}

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        captured["timeout_s"] = timeout_s
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    ctx = _ctx(tmp_path)  # no deadline_box, coder_time_left() is None
    _plant_authored_handler(tmp_path / "build")
    assert _run_writer_via_codewhale_worker(ctx).ok is True
    assert captured["timeout_s"] is None


def test_stage_1_inspect_bumps_an_in_flight_writer_instead_of_stopping(tmp_path):
    """The interaction that IS the incident: once the writer wall outlives
    the stage-1 boundary, the budget inspector sees the CLI still in flight
    and BUMPS the wall (continue_stage_2) rather than hard-stopping a corpse.
    Pins budget_inspect.py:400-409 so the D1 fix cannot be undone from the
    inspector side."""
    from app.factory.build.budget_inspect import STAGE_1_S, STAGE_2_S, inspect_decision

    out = inspect_decision(
        stage="stage_1",
        snapshot={
            "cli_in_flight": True,
            "model_call_deadline_s": 5400.0,
            "agent_written": 3,
            "stub_rate": 0.4,
        },
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        state={},
        workspace=tmp_path,
    )
    assert out["decision"] == "continue_stage_2", out
    assert out["next_wall_s"] == STAGE_2_S
    assert "not FACTORY_CODE_CLI_UNUSED" in out["reason"]


def test_worker_dispatch_records_the_receipt(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(ctx)
    assert result.ok is True
    assert result.notes["codewhale_worker"]["status"] == "completed"
    # E2: the Floor monitor receipt reflects the leg that ran.
    receipt_file = tmp_path / "build" / "docs" / "coder_receipt.json"
    assert receipt_file.is_file()
    import json as _json
    payload = _json.loads(receipt_file.read_text(encoding="utf-8"))
    assert payload["via"] == "codewhale_worker"


def test_worker_succeeded_but_wrote_nothing_is_refused(tmp_path, monkeypatch):
    """E1: a 'completed' receipt with zero tool calls or zero stamped
    handlers is the same silent success the writer_contract gate refuses â€”
    the role must refuse it, not report ok=True."""
    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        return _receipt(tools=[])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert result.ok is False
    assert "writer_no_output" in result.detail


def test_worker_succeeded_with_tools_but_no_stamped_handlers_is_refused(
    tmp_path, monkeypatch
):
    """E1: tool calls alone are not authorship â€” the disk-level stamp
    count is what counts."""
    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        return _receipt(tools=[{"tool": "write", "path": "notes.txt"}])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert result.ok is False
    assert "writer_no_output" in result.detail


def test_worker_failure_raises_a_named_role_error(tmp_path, monkeypatch):
    def boom(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        raise WorkerError("worker_exec_failed: nope")

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", boom
    )
    with pytest.raises(RoleError) as exc:
        _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert "codewhale_worker_failed" in str(exc.value)


def test_codewhale_stamp_counts_as_agent_output(tmp_path):
    """The worker's stamped handlers satisfy the Phase 0.5 artifact gate."""
    from app.factory.build.authorship import agent_written_handler_ids_in_workspace

    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "cap.py").write_text(
        '"""Handler for capability cap.\n\n'
        "Written by the factory WRITER role (codewhale exec).\n"
        '"""\n',
        encoding="utf-8",
    )
    assert agent_written_handler_ids_in_workspace(tmp_path) == ["cap"]

def test_worker_output_in_staging_survives_commit(tmp_path, monkeypatch):
    """Regression: the worker writes into the staging tree directly; those
    files are not in the tracked ``written`` list, so commit() used to drop
    every authored handler (live-factory sess_620b8581fb224bea: the role
    counted authored=8, the gate counted 0). The role must register them so
    the pass's own output travels with the commit."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    from pathlib import Path

    ws = RoleWorkspace(
        BuildRole.WRITER,
        tmp_path / "build",
        staging=tmp_path / ".factory-staging" / "writer",
    )
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=type(
            "B",
            (),
            {"product_id": "p", "product_name": "n", "vertical": "v", "summary": "s"},
        )(),
        plan=None,
        state={},
    )

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        # The worker subprocess writes directly into the staging dir.
        actions = Path(dest) / "app" / "actions"
        actions.mkdir(parents=True, exist_ok=True)
        (actions / "cap.py").write_text(
            '"""Handler for capability cap.\n\n'
            "Written by the factory WRITER role (codewhale exec).\n"
            '"""\n',
            encoding="utf-8",
        )
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(ctx)
    assert result.ok is True
    ws.commit()

    dest_handler = ws.destination / "app" / "actions" / "cap.py"
    assert dest_handler.is_file(), "worker-authored handler was dropped by commit"
    dest_receipt = ws.destination / "docs" / "coder_receipt.json"
    assert dest_receipt.is_file(), "worker receipt was dropped by commit"


def test_tenant_capped_writer_waits_then_runs(tmp_path, monkeypatch):
    """Live 2026-09-30: the automotive re-run died terminally on
    tenant_slots_exhausted while the owner's other build held the slot.
    A tenant-scope cap is the owner's own other build -- transient. The
    WRITER waits (narrated, bounded), it does not bury the run."""
    import time as _time

    from app.factory.build.codewhale_worker import TENANT_SLOTS_EXHAUSTED

    calls = {"n": 0}
    naps = []

    def capped_twice(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise WorkerError(
                f"worker_concurrency_capped: {TENANT_SLOTS_EXHAUSTED} "
                "scope=tenant — tenant deadbeef holds 1/1"
            )
        return _receipt([{"tool": "write_file"}])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", capped_twice
    )
    monkeypatch.setattr(_time, "sleep", lambda s: naps.append(s))
    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")
    notes = []
    ctx.note = lambda text, **kw: notes.append(str(text))

    result = _run_writer_via_codewhale_worker(ctx)
    assert result.ok, result.detail
    assert calls["n"] == 3
    assert naps, "the retry must wait between attempts, not spin"
    assert any("wait" in n.lower() for n in notes), notes


def test_process_capped_writer_fails_immediately(tmp_path, monkeypatch):
    """A full box is other tenants' work -- refuse fast, never camp on it."""
    from app.factory.build.codewhale_worker import PROCESS_SLOTS_EXHAUSTED

    calls = {"n": 0}

    def box_full(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        calls["n"] += 1
        raise WorkerError(
            f"worker_concurrency_capped: {PROCESS_SLOTS_EXHAUSTED} scope=process — 3/3"
        )

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", box_full
    )
    with pytest.raises(RoleError) as exc:
        _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert "codewhale_worker_failed" in str(exc.value)
    assert calls["n"] == 1


def test_slot_wait_budget_zero_fails_without_retry(tmp_path, monkeypatch):
    from app.factory.build.codewhale_worker import TENANT_SLOTS_EXHAUSTED

    monkeypatch.setenv("FACTORY_WORKER_SLOT_WAIT_S", "0")
    calls = {"n": 0}

    def capped(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None):
        calls["n"] += 1
        raise WorkerError(
            f"worker_concurrency_capped: {TENANT_SLOTS_EXHAUSTED} scope=tenant — 1/1"
        )

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", capped
    )
    with pytest.raises(RoleError):
        _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert calls["n"] == 1
