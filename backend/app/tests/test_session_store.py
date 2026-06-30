import os
import tempfile
from pathlib import Path

import pytest

from app.core import session_store
from app.core.session_persistence import _backup_path, _state_path
from app.models.session import SessionState


@pytest.fixture(autouse=True)
def _clear_sessions(monkeypatch, tmp_path):
    """Use a temporary storage directory and clear the in-memory store."""
    monkeypatch.setattr(session_store, "_session_store", {})
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    # Re-import session_persistence picks up STORAGE_PATH from env at import
    # time, so patch its constant directly as well.
    import app.core.session_persistence as sp
    monkeypatch.setattr(sp, "STORAGE_PATH", str(tmp_path / "storage"))
    yield


@pytest.mark.asyncio
async def test_create_and_get_session():
    state = session_store.create_session("sess_create", "u1")
    assert state.session_id == "sess_create"
    assert state.user_id == "u1"

    # Simulate restart: drop in-memory store and reload.
    session_store._session_store.clear()
    restored = session_store.get_session("sess_create")
    assert restored is not None
    assert restored.session_id == "sess_create"
    assert restored.user_id == "u1"


@pytest.mark.asyncio
async def test_update_session_persists():
    state = session_store.create_session("sess_update", "u2")
    state.phase = 3
    state.phase_status = "completed"
    session_store.update_session("sess_update", state)

    session_store._session_store.clear()
    restored = session_store.get_session("sess_update")
    assert restored.phase == 3
    assert restored.phase_status == "completed"


@pytest.mark.asyncio
async def test_large_fields_excluded_from_snapshot():
    state = session_store.create_session("sess_large", "u3")
    state.chat_history = [{"role": "user", "content": "hello"}]
    state.training_data = [{"question": "q", "answer": "a"}]
    session_store.update_session("sess_large", state)

    # Snapshot should exist but not contain the large fields.
    snapshot_path = _state_path("sess_large")
    assert snapshot_path.exists()
    text = snapshot_path.read_text(encoding="utf-8")
    assert "chat_history" not in text
    assert "training_data" not in text

    # Restored state should have empty defaults for excluded fields.
    session_store._session_store.clear()
    restored = session_store.get_session("sess_large")
    assert restored.chat_history == []
    assert restored.training_data == []


@pytest.mark.asyncio
async def test_atomic_write_creates_backup():
    state = session_store.create_session("sess_atomic", "u4")
    session_store.update_session("sess_atomic", state)

    original = _state_path("sess_atomic").read_text(encoding="utf-8")
    state.phase = 4
    session_store.update_session("sess_atomic", state)

    backup = _backup_path("sess_atomic")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_corrupt_snapshot_falls_back_to_backup():
    state = session_store.create_session("sess_corrupt", "u5")
    session_store.update_session("sess_corrupt", state)

    # Corrupt the main file but leave the backup intact.
    _state_path("sess_corrupt").write_text("not json", encoding="utf-8")

    session_store._session_store.clear()
    restored = session_store.get_session("sess_corrupt")
    assert restored is not None
    assert restored.session_id == "sess_corrupt"


@pytest.mark.asyncio
async def test_unknown_version_is_skipped():
    state = session_store.create_session("sess_version", "u6")
    session_store.update_session("sess_version", state)

    # Remove the backup so the corrupt main file is the only candidate.
    backup = _backup_path("sess_version")
    if backup.exists():
        backup.unlink()

    path = _state_path("sess_version")
    data = path.read_text(encoding="utf-8")
    data = data.replace('"version": 1', '"version": 999')
    path.write_text(data, encoding="utf-8")

    session_store._session_store.clear()
    restored = session_store.get_session("sess_version")
    assert restored is None
