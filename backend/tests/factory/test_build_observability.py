"""A long build must be observable, and a dead build must not look alive.

New-shape tests for two defects found while watching the FIRST live runner
build on production:

1. The ledger only records phase boundaries, so a WRITER pass of ~16 agent
   calls reported a frozen "2/5" for twenty-plus minutes. A customer -- and
   an operator debugging it -- could not distinguish work from a hang.
   Worse, nothing was in the logs either: no logging was configured, so
   every ``logging.getLogger("cerebrumdev...")`` line was dropped while
   uvicorn's access logs printed. The build was completely unobservable.

2. A build whose thread dies (worker restart, redeploy, OOM) leaves the
   ledger's last event as PHASE_STARTED forever, which read as "building"
   for eternity -- an infinite spinner for a build that no longer exists.

Progress is recorded as NOTE events, which must NOT disturb the verdict
readers: a progress line can never be mistaken for a gate result.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import RoleContext
from app.factory.build.runner import RoleRunner
from app.factory.build_jobs import build_status

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_note_is_a_noop_without_a_progress_sink():
    """Roles stay testable without a ledger, and telemetry never fails a
    build: a raising sink is swallowed."""
    ctx = RoleContext(role=BuildRole.WRITER, workspace=None, blueprint=None, plan=None)
    ctx.note("nothing is listening")  # must not raise

    def _boom(detail, payload):
        raise RuntimeError("telemetry backend down")

    ctx.progress = _boom
    ctx.note("still must not raise")


def test_a_build_records_intra_phase_progress(tmp_path):
    """The WRITER must say what it is doing while it does it."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    assert runner.run().ok

    notes = [e for e in runner.ledger.events() if e.kind is EventKind.NOTE]
    assert notes, "the build reported no intra-phase progress at all"

    stages = {(n.payload or {}).get("stage") for n in notes}
    assert "handlers" in stages, stages
    assert "routes" in stages, stages
    # Every progress line must be attributable and countable, or a UI cannot
    # render it as progress.
    for note in notes:
        assert note.role is not None
        assert note.detail
        payload = note.payload or {}
        if payload.get("stage") in ("handlers", "routes"):
            assert isinstance(payload.get("done"), int)
            assert isinstance(payload.get("total"), int)


def test_progress_notes_do_not_disturb_the_verdict_readers(tmp_path):
    """NOTE is not a verdict. completed_roles / terminal_event / succeeded
    must read exactly as they did before progress existed."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    outcome = runner.run()
    assert outcome.ok

    ledger = BuildLedger(out / "build_ledger.jsonl")
    assert ledger.succeeded()
    assert ledger.terminal_event().kind is EventKind.RUN_SUCCEEDED
    assert len(ledger.completed_roles()) == 5
    # And a killed-phase reader must not treat a NOTE as an unfinished phase.
    assert ledger.interrupted_role() is None


def test_status_surfaces_the_current_activity(tmp_path):
    """What the client polls while waiting."""
    out = tmp_path / "build"
    ledger = BuildLedger(out / "build_ledger.jsonl")
    out.mkdir(parents=True, exist_ok=True)
    ledger.start_run(product_id="probe", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler defect_register (coder LLM (kimi-k2.7-code))",
        payload={"stage": "handlers", "done": 2, "total": 5},
    )

    status = build_status(out)
    assert status["state"] == "building"
    assert "defect_register" in status["activity"]
    assert status["activity_stage"] == "handlers"
    assert status["activity_done"] == 2
    assert status["activity_total"] == 5


def test_a_build_with_no_process_behind_it_reports_stalled(tmp_path):
    """A redeploy kills the build thread. The ledger's last event stays
    PHASE_STARTED, which used to read as "building" forever -- an infinite
    spinner for a build that cannot finish."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="probe", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")

    assert build_status(out)["state"] == "building"

    # Age the ledger past the stall threshold, exactly as a dead build looks.
    from app.factory.build_jobs import _STALL_AFTER_S

    old = time.time() - (_STALL_AFTER_S + 120)
    os.utime(out / "build_ledger.jsonl", (old, old))

    status = build_status(out)
    assert status["state"] == "stalled", status
    assert "generate again" in status["detail"]
    # A stalled build is not downloadable either.
    from app.factory.build_jobs import is_build_complete

    assert not is_build_complete(out)


def test_application_logs_reach_stdout():
    """The operability half: app loggers had no handler in production, so a
    long build emitted nothing diagnosable.

    Asserted on the configured logging tree rather than captured output --
    pytest installs its own capture handlers, which would pass even with no
    application handler at all (a false green about the very thing under
    test)."""
    import logging

    from app.main import _configure_logging

    _configure_logging()
    root = logging.getLogger()
    assert root.level <= logging.INFO, logging.getLevelName(root.level)
    stdout_handlers = [
        h for h in root.handlers if getattr(h, "_cerebrum_stdout", False)
    ]
    assert stdout_handlers, "no application stdout handler was installed"
    assert stdout_handlers[0].stream is sys.stdout

    # And an app logger must actually be enabled for INFO through that tree.
    assert logging.getLogger("cerebrumdev.factory.probe").isEnabledFor(logging.INFO)

    # Idempotent: repeated calls (reload, import cycles) must not stack
    # handlers and print every line N times.
    _configure_logging()
    assert len(
        [h for h in logging.getLogger().handlers if getattr(h, "_cerebrum_stdout", False)]
    ) == 1


def test_the_coder_yields_when_the_build_budget_is_nearly_spent(tmp_path):
    """The wall clock is only checked BETWEEN phases, so a slow model can run
    the WRITER unbounded -- a production build sat in it for 39 minutes.

    With little budget left the coder must not start another call: it hands
    the artifact to the deterministic template and RECORDS the skip, so the
    build finishes honestly instead of hanging.
    """
    import time as _time

    from app.factory.build.roles import _budget_too_low

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=None,
        plan=None,
        deadline=_time.monotonic() + 5,  # 5s left: no call can finish
    )
    assert _budget_too_low(ctx, "handler") is True
    failures = ctx.state["coder_failures"]
    assert "handler" in failures
    assert "budget" in failures["handler"]

    # Plenty of budget: the coder runs normally.
    roomy = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=None,
        plan=None,
        deadline=_time.monotonic() + 3600,
    )
    assert _budget_too_low(roomy, "handler") is False
    assert not roomy.state.get("coder_failures")

    # No deadline configured means unbounded, as before.
    unbounded = RoleContext(
        role=BuildRole.WRITER, workspace=None, blueprint=None, plan=None
    )
    assert _budget_too_low(unbounded, "handler") is False


def test_a_read_timeout_is_not_retried(monkeypatch):
    """Retrying a read timeout cost the same wait again for the same likely
    outcome: 3 attempts x 2 model legs x 180s = up to 18 minutes for ONE
    artifact, which is what stalled the first production build. Connection
    failures stay retryable."""
    import httpx

    from app.factory import coder

    calls = []

    def _timeout(url, json=None, headers=None, timeout=None):
        calls.append(timeout)
        raise httpx.ReadTimeout("model did not answer")

    monkeypatch.setattr(coder.httpx, "post", _timeout)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {
            "provider": "kimi",
            "model": "primary",
            "fallback_model": "fallback",
            "base_url": "https://api.moonshot.ai/v1",
            "api_key": "test-key-not-real",
        },
    )

    with pytest.raises(coder.CoderError):
        coder._llm_code_call([{"role": "user", "content": "u"}])

    # One attempt per model leg, not three.
    assert len(calls) == 2, calls
    # And the per-call wait is bounded and configurable.
    assert all(t <= 180 for t in calls), calls
