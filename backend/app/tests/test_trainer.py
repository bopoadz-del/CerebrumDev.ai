"""Tests for the Tinker fine-tuning orchestrator."""

import asyncio
import time
from datetime import datetime, timedelta
from typing import List

import pytest

import app.core.session_store as session_store
from app.core.session_store import create_session, get_session, update_session
from app.core.trainer import (
    TINKER_API_KEY as TRAINER_KEY,
    cancel_training,
    get_training_status,
    start_training,
    validate_training_data,
)


def _make_pairs(n: int = 10) -> List[dict]:
    return [
        {"question": f"Question {i}?", "answer": f"Answer {i}."}
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Keep the in-memory session store clean between tests."""
    from app.core import session_store
    session_store._session_store.clear()
    yield
    session_store._session_store.clear()


def test_validate_training_data_ok():
    pairs = _make_pairs(10)
    assert validate_training_data(pairs) is True


def test_validate_training_data_too_few():
    with pytest.raises(ValueError, match="Need at least 10"):
        validate_training_data(_make_pairs(3))


def test_validate_training_data_empty_pair():
    pairs = _make_pairs(10)
    pairs[5]["answer"] = ""
    with pytest.raises(ValueError, match="Pair 6 has empty"):
        validate_training_data(pairs)


@pytest.mark.asyncio
async def test_start_training_without_key_raises(monkeypatch):
    monkeypatch.setattr("app.core.trainer.TINKER_API_KEY", "")
    session = create_session("s-no-key", "tester")
    session.training_data = _make_pairs(10)
    with pytest.raises(Exception) as exc_info:
        await start_training("s-no-key")
    assert "TINKER_API_KEY" in str(exc_info.value)


@pytest.mark.asyncio
async def test_start_training_without_data_raises(monkeypatch):
    monkeypatch.setattr("app.core.trainer.TINKER_API_KEY", "tk-test")
    create_session("s-no-data", "tester")
    with pytest.raises(Exception) as exc_info:
        await start_training("s-no-data")
    assert "empty" in str(exc_info.value).lower() or "Training data" in str(exc_info.value)


@pytest.mark.asyncio
async def test_start_and_poll_training_status(monkeypatch):
    """Training thread completes quickly; status ends as completed."""
    completed_path = "tinker://s-ok:train:0/checkpoints/model-final"

    def fake_run(session_id, job_id, rows, base_model, lora_rank, batch_size, max_steps, lr):
        from app.core import session_store
        state = session_store.get_session(session_id)
        state.training_job.status = "running"
        state.training_job.progress = 0.5
        session_store.update_session(session_id, state)
        time.sleep(0.05)
        state = session_store.get_session(session_id)
        state.training_job.status = "completed"
        state.training_job.progress = 1.0
        state.training_job.fine_tuned_model_id = completed_path
        session_store.update_session(session_id, state)

    monkeypatch.setattr("app.core.trainer._run_training", fake_run)
    monkeypatch.setattr("app.core.trainer.TINKER_API_KEY", "tk-test")

    session = create_session("s-ok", "tester")
    session.training_data = _make_pairs(10)

    job = await start_training("s-ok")
    assert job.provider == "tinker"
    assert job.job_id.startswith("tinker-")
    assert job.status in ("queued", "running")
    assert job.dataset_size == 10

    # Wait briefly for the fake thread to finish.
    for _ in range(50):
        await asyncio.sleep(0.01)
        status = await get_training_status("s-ok")
        if status.status == "completed":
            break

    status = await get_training_status("s-ok")
    assert status.status == "completed"
    assert status.progress == 1.0
    assert status.fine_tuned_model_id == completed_path


@pytest.mark.asyncio
async def test_cancel_training(monkeypatch):
    """Cancel an in-flight training job."""
    stop_after = {"iterations": 0}

    def fake_run(session_id, job_id, rows, base_model, lora_rank, batch_size, max_steps, lr):
        from app.core.trainer import _cancel_events
        event = _cancel_events.get(session_id)
        state = get_session(session_id)
        state.training_job.status = "running"
        # Do not rely on get_session/update_session here; the test will mutate via store.
        for _ in range(200):
            if event and event.is_set():
                state = get_session(session_id)
                state.training_job.status = "cancelled"
                get_session.__module__  # keep import
                from app.core import session_store
                session_store.update_session(session_id, state)
                return
            time.sleep(0.01)
        stop_after["iterations"] += 1

    monkeypatch.setattr("app.core.trainer._run_training", fake_run)
    monkeypatch.setattr("app.core.trainer.TINKER_API_KEY", "tk-test")

    session = create_session("s-cancel", "tester")
    session.training_data = _make_pairs(10)

    await start_training("s-cancel")
    # Give the thread a moment to enter running state.
    await asyncio.sleep(0.05)
    ok = await cancel_training("s-cancel")
    assert ok is True

    status = await get_training_status("s-cancel")
    assert status.status == "cancelled"


@pytest.mark.asyncio
async def test_training_watchdog_fails_stale_running_job(monkeypatch, tmp_path):
    """A running job older than TRAINING_MAX_RUNTIME_S is marked failed on read."""
    import app.core.session_persistence as sp
    import app.core.session_store as session_store_module

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(sp, "STORAGE_PATH", str(tmp_path / "storage"))

    # Use a very short timeout so a job from the recent past is considered stale.
    monkeypatch.setattr(session_store_module, "TRAINING_MAX_RUNTIME_S", 1)

    session = create_session("s-stale", "tester")
    session.training_data = _make_pairs(10)
    session.training_job.status = "running"
    session.training_job.started_at = datetime.utcnow() - timedelta(seconds=3600)
    update_session("s-stale", session)

    # get_session should trigger the watchdog.
    state = get_session("s-stale")
    assert state is not None
    assert state.training_job.status == "failed"
    assert state.training_job.error == "exceeded max runtime or interrupted by restart"

    # After a restart (cleared memory), get_training_status should also catch it.
    session_store._session_store.clear()
    status = await get_training_status("s-stale")
    assert status.status == "failed"
    assert status.error == "exceeded max runtime or interrupted by restart"
