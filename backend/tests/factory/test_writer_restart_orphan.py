"""Restart-mid-WRITER must not leave a silent 7230s model_call zombie.

Live evidence (2026-09-07 ~04:46Z, tip d4a4029 / #382, sess_05914670d8f34533,
estate-management / Estate Steward): build-status stayed
state=building, model_call_in_progress=true, deadline_s=7230 after the
Render deploy killed the WRITER thread (last_event_at frozen at 04:09:21Z,
same minute as dep-daf3htmq). coder_brief and coder_log were absent.
"""

from __future__ import annotations

import inspect
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.factory.blueprint import CapabilitySpec, ProductBlueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.orphan_recovery import (
    ORPHAN_FAIL_DETAIL,
    fail_orphaned_model_call,
    is_orphaned_inflight_workspace,
    recover_orphaned_model_calls,
    session_id_from_output,
)
from app.factory.build.runner import blueprint_hash
from app.factory.build_jobs import (
    FACTORY_CODE_CLI_ORPHANED,
    build_status,
    start_runner_build,
)


@contextmanager
def _hold_build_thread(product_id: str):
    stop = threading.Event()
    thread = threading.Thread(
        target=stop.wait, name=f"build-{product_id}", daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def _estate_bp(product_id: str = "estate-management") -> ProductBlueprint:
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id=product_id,
        product_name="Estate Steward",
        vertical="estate",
        summary="private estate operations",
        capabilities=[
            CapabilitySpec(
                id="document_vault",
                description="vault",
                block_ids=["storage"],
                strategy_hint="REUSE",
            )
        ],
    )


def _age_ledger(path: Path, age_s: float) -> None:
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=age_s)
    ).isoformat(timespec="seconds")
    aged = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    path.write_text("\n".join(aged) + "\n", encoding="utf-8")


def _estate_zombie_ledger(
    out: Path,
    *,
    product_id: str = "estate-management",
    inputs_hash: str = "abc",
    age_s: float = 2200.0,
) -> BuildLedger:
    """COLLECTOR+CLONER done, WRITER dispatch model_call, no live worker."""
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product_id, inputs_hash=inputs_hash)
    ledger.append(
        EventKind.PHASE_STARTED, role=BuildRole.COLLECTOR, detail="COLLECTOR"
    )
    ledger.append(
        EventKind.GATE_PASSED, role=BuildRole.COLLECTOR, detail="COLLECTOR"
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.CLONER, detail="CLONER")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.CLONER, detail="CLONER")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": 7230.0,
            "done": 0,
            "total": 1,
        },
    )
    _age_ledger(out / "build_ledger.jsonl", age_s)
    return ledger


def test_orphaned_writer_model_call_is_not_in_progress(tmp_path):
    """sess_05914670d8f34533: no thread ⇒ not model_call_in_progress."""
    out = tmp_path / "estate-management"
    _estate_zombie_ledger(out)
    status = build_status(out)
    assert status["state"] == "stalled", status
    assert status.get("model_call_in_progress") is not True
    assert status.get("honesty") == FACTORY_CODE_CLI_ORPHANED
    assert FACTORY_CODE_CLI_ORPHANED in status["detail"]
    assert status["pilot_ready"] is False
    assert status["current_phase"]["id"] == "WRITER"
    assert status["phases_done"] == 2
    assert is_orphaned_inflight_workspace(out) is True


def test_live_writer_thread_still_claims_in_progress(tmp_path):
    out = tmp_path / "estate-management"
    _estate_zombie_ledger(out, age_s=1896)
    with _hold_build_thread("estate-management"):
        status = build_status(out)
        assert status["state"] == "building", status
        assert status["model_call_in_progress"] is True
        assert status["model_call_deadline_s"] == 7230.0
        assert is_orphaned_inflight_workspace(out) is False


def test_start_runner_build_resumes_orphaned_writer(tmp_path, monkeypatch):
    """Stalled orphan is a resume source — same ledger, gated C-BRIEF again."""
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "runner")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready",
        lambda: None,
    )
    bp = _estate_bp()
    digest = blueprint_hash(bp)
    out = tmp_path / "estate-management"
    _estate_zombie_ledger(out, inputs_hash=digest)

    held = threading.Event()
    started = []

    def _hold(*_a, **_k):
        started.append(1)
        held.wait(timeout=5)

    monkeypatch.setattr("app.factory.build_jobs._run", _hold)
    try:
        result = start_runner_build(bp, out)
        assert result["already_running"] is False
        assert result["output_dir"] == str(out)
        assert result.get("fresh_workspace") is False
        assert started == [1]
        status = build_status(out)
        assert status["state"] == "building", status
        assert status.get("model_call_in_progress") is True
    finally:
        held.set()


def test_recover_resumes_when_session_blueprint_present(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready",
        lambda: None,
    )
    bp = _estate_bp()
    digest = blueprint_hash(bp)
    session_id = "sess_05914670d8f34533"
    out = (
        tmp_path
        / "factory_outputs"
        / "sessions"
        / session_id
        / "estate-management"
    )
    _estate_zombie_ledger(out, inputs_hash=digest)
    state_dir = tmp_path / "storage" / "sessions" / session_id
    state_dir.mkdir(parents=True)
    state_dir.joinpath("state.json").write_text(
        json.dumps(
            {
                "version": 2,
                "session_id": session_id,
                "user_id": "owner",
                "product_design": {"blueprint": bp.model_dump(mode="json")},
            }
        ),
        encoding="utf-8",
    )

    held = threading.Event()
    started = []

    def _hold(*_a, **_k):
        started.append(1)
        held.wait(timeout=5)

    monkeypatch.setattr("app.factory.build_jobs._run", _hold)
    try:
        results = recover_orphaned_model_calls(
            outputs_root=tmp_path / "factory_outputs",
            storage_root=tmp_path / "storage",
        )
        assert len(results) == 1, results
        assert results[0]["action"] == "resumed", results
        assert started == [1]
        assert session_id_from_output(out) == session_id
        status = build_status(out)
        assert status["state"] == "building", status
        assert status.get("model_call_in_progress") is True
    finally:
        held.set()


def test_recover_fail_closes_without_blueprint(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    session_id = "sess_05914670d8f34533"
    out = (
        tmp_path
        / "factory_outputs"
        / "sessions"
        / session_id
        / "estate-management"
    )
    _estate_zombie_ledger(out)
    results = recover_orphaned_model_calls(
        outputs_root=tmp_path / "factory_outputs",
        storage_root=tmp_path / "storage",
    )
    assert len(results) == 1, results
    assert results[0]["action"] == "failed"
    assert results[0]["honesty"] == FACTORY_CODE_CLI_ORPHANED
    status = build_status(out)
    assert status["state"] == "failed", status
    assert FACTORY_CODE_CLI_ORPHANED in status["detail"]
    assert status.get("model_call_in_progress") is not True
    assert status["pilot_ready"] is False
    assert is_orphaned_inflight_workspace(out) is False


def test_fail_orphaned_writes_terminal_coder_stopped(tmp_path):
    out = tmp_path / "estate-management"
    _estate_zombie_ledger(out)
    result = fail_orphaned_model_call(out, detail=ORPHAN_FAIL_DETAIL)
    assert result["action"] == "failed"
    status = build_status(out)
    assert status["state"] == "failed"
    assert "coding agent stopped" in status["detail"].lower() or (
        FACTORY_CODE_CLI_ORPHANED in status["detail"]
    )
    assert status.get("model_call_in_progress") is not True


def test_recover_skips_live_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    out = (
        tmp_path
        / "factory_outputs"
        / "sessions"
        / "sess_live"
        / "estate-management"
    )
    _estate_zombie_ledger(out, age_s=400)
    with _hold_build_thread("estate-management"):
        results = recover_orphaned_model_calls(
            outputs_root=tmp_path / "factory_outputs"
        )
    assert results == []


def test_lifespan_recovers_orphaned_model_calls():
    from app.main import _lifespan

    src = inspect.getsource(_lifespan)
    assert "recover_orphaned_model_calls" in src
    assert "ensure_code_cli_credentials" in src
