"""The Floor shows what the writer is doing, for the whole pass.

Owner, seven minutes into a live build: "i dont see any updates for the
writer". Two separate causes, both measured on live runs:

1. The agent's first stretch -- reading the brief and the cloned blocks --
   writes no STEP line and no file for ~10 minutes (STEP 1 landed at 9m16s
   on one build and 10m01s on the next). The factory said nothing either.
2. When the agent does write its progress log it writes a burst, and the
   relay's 3s throttle DISCARDED everything after the first line: one live
   log read "STEP 1" and then "STEP 21".
"""

from __future__ import annotations

import io
import json
import time
from unittest import mock

from app.factory.build import codewhale_worker
from app.factory.build.codewhale_worker import (
    HEARTBEAT_PREFIX,
    count_authored_files,
    heartbeat_line,
    is_narration_line,
)
from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
from app.factory.build.tenant_bind import bind_tenant_store
from tests.factory.test_codewhale_writer_dispatch import (
    _ctx,
    _plant_authored_handler,
    _receipt,
)


def _run_role(tmp_path, monkeypatch, lines):
    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")
    notes: list[str] = []
    ctx.progress = lambda detail, payload: notes.append(detail)

    def fake_run(prompt, dest, tenant_store=None, session_id="", progress=None):
        for line in lines:  # a burst: no time passes between lines
            progress(line, {})
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    assert _run_writer_via_codewhale_worker(ctx).ok is True
    return notes


def test_a_burst_of_step_lines_is_shown_in_full(tmp_path, monkeypatch):
    burst = [f"writer: STEP {i}: did thing {i}" for i in range(1, 21)]

    notes = _run_role(tmp_path, monkeypatch, burst)

    shown = [n for n in notes if n.startswith("writer: STEP")]
    assert shown == burst, "steps that happened were discarded by the throttle"


def test_chatty_cli_lines_are_still_throttled(tmp_path, monkeypatch):
    """The throttle keeps its job: CLI log noise does not flood the ledger."""
    noise = [f"engine turn completion settled status=ok n={i}" for i in range(50)]

    notes = _run_role(tmp_path, monkeypatch, noise)

    assert len([n for n in notes if n.startswith("engine turn")]) == 1


def test_noise_cannot_starve_a_step_line(tmp_path, monkeypatch):
    lines = ["engine turn a", "writer: STEP 1: inventory read", "engine turn b"]

    notes = _run_role(tmp_path, monkeypatch, lines)

    assert "writer: STEP 1: inventory read" in notes


def test_the_heartbeat_only_states_what_the_factory_observed():
    quiet = heartbeat_line(245, files=0, steps_seen=0)
    assert quiet.startswith(HEARTBEAT_PREFIX)
    assert "4m05s" in quiet and "No files yet" in quiet
    working = heartbeat_line(725, files=37, steps_seen=12)
    assert "12m05s" in working and "37 file(s)" in working and "12 step(s)" in working
    # It never names a step the agent did not report.
    assert "STEP" not in quiet and "STEP" not in working
    assert is_narration_line(quiet) and is_narration_line("writer: STEP 2: x")
    assert not is_narration_line("engine turn completion settled")


def test_authored_files_exclude_vendor_and_the_progress_logs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "vendor" / "blocks" / "b").mkdir(parents=True)
    (tmp_path / "vendor" / "blocks" / "b" / "block.py").write_text("x", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "writer_progress.log").write_text("STEP 1", encoding="utf-8")
    (tmp_path / "docs" / "writer_progress.jsonl").write_text("{}", encoding="utf-8")

    assert count_authored_files(tmp_path) == 1


def test_a_silent_cli_gets_a_heartbeat_and_a_talking_one_does_not(tmp_path, monkeypatch):
    """End to end through run_worker_job with a CLI that stays silent."""
    monkeypatch.setattr(codewhale_worker, "HEARTBEAT_EVERY_S", 0.6)

    class _SilentProc:
        def __init__(self):
            self.stdout = io.StringIO(
                json.dumps({"status": "completed", "termination_reason": "resolved"}) + "\n"
            )
            self.stderr = io.StringIO("")
            self.returncode = None
            self._until = time.monotonic() + 2.2

        def wait(self, timeout=None):
            import subprocess

            if time.monotonic() < self._until:
                time.sleep(min(timeout or 0.1, 0.1))
                raise subprocess.TimeoutExpired("codewhale", timeout)
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = 9

    relayed: list[str] = []
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen", side_effect=lambda *a, **k: _SilentProc()):
        receipt = codewhale_worker.run_worker_job(
            "write the platform",
            tmp_path / "checkout",
            tenant_store=bind_tenant_store("acct-heartbeat"),
            progress=lambda line, info: relayed.append(line),
        )

    assert receipt.status == "completed"
    beats = [ln for ln in relayed if ln.startswith(HEARTBEAT_PREFIX)]
    assert len(beats) >= 2, relayed
    assert "No files yet" in beats[0]
