import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from app.core.packager import package_session
from app.models.session import SessionState, TrainingJob


@pytest.fixture(autouse=True)
def _use_temp_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("TINKER_API_KEY", "test-tinker-key")
    import app.core.packager as packager
    monkeypatch.setattr(packager, "STORAGE_PATH", str(tmp_path / "storage"))


def _make_state(session_id: str = "sess_pkg") -> SessionState:
    state = SessionState(session_id=session_id, user_id="u1")
    state.phase = 4
    state.proposed_chain = {"blocks": [], "connections": []}
    return state


def test_package_copies_tinker_adapter():
    state = _make_state()
    result = package_session(state)
    adapter = Path(result["package_dir"]) / "app" / "core" / "llm" / "tinker_adapter.py"
    assert adapter.exists()


def test_package_sets_grounded_adapter_env_for_tinker_model():
    state = _make_state()
    state.training_job = TrainingJob(
        provider="tinker",
        job_id="job-1",
        status="completed",
        fine_tuned_model_id="tinker://sess_pkg:train:0/sampler_weights/adapter",
        dataset_size=12,
        started_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    result = package_session(state)
    env = result["env_vars"]
    assert env["GROUNDED_ADAPTER_ENABLED"] == "true"
    assert env["GROUNDED_ADAPTER_TINKER_PATH"] == state.training_job.fine_tuned_model_id
    assert "TINKER_API_KEY" in env


def test_package_does_not_set_tinker_env_for_untrained_session():
    state = _make_state()
    result = package_session(state)
    env = result["env_vars"]
    assert env.get("GROUNDED_ADAPTER_ENABLED") != "true"
    assert "GROUNDED_ADAPTER_TINKER_PATH" not in env
