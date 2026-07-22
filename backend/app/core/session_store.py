import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from ..models.session import SessionState, UploadResult
from .session_persistence import load_session_state, save_session_state

logger = logging.getLogger(__name__)

_session_store: Dict[str, SessionState] = {}

TRAINING_MAX_RUNTIME_S = int(os.getenv("TRAINING_MAX_RUNTIME_S", "7200"))


def _apply_training_watchdog(state: SessionState) -> bool:
    """Fail a stale running training job and return True if a change was made."""
    job = state.training_job
    if job.status != "running" or not job.started_at:
        return False

    # started_at may be a datetime or an ISO string after snapshot reload.
    if isinstance(job.started_at, str):
        try:
            started = datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        started = job.started_at

    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if (now - started).total_seconds() > TRAINING_MAX_RUNTIME_S:
        job.status = "failed"
        job.error = "exceeded max runtime or interrupted by restart"
        job.updated_at = now.replace(tzinfo=None)
        return True
    return False


def create_session(session_id: str, user_id: str) -> SessionState:
    state = SessionState(session_id=session_id, user_id=user_id)
    _session_store[session_id] = state
    save_session_state(state)
    try:
        from .accounts_store import record_session_owner

        record_session_owner(session_id, user_id)
    except Exception as exc:  # noqa: BLE001 — ownership index must never break sessions
        logger.warning("Could not record session owner for %s: %s", session_id, exc)
    return state


def _rehydrate_from_chroma(session_id: str) -> Optional[SessionState]:
    """Rebuild a session from its persisted ChromaDB collection if it exists."""
    try:
        from .chroma_store import load_session_upload
        data = load_session_upload(session_id)
    except Exception as exc:
        logger.warning("Could not query ChromaDB for session %s: %s", session_id, exc)
        return None

    if not data:
        return None

    state = SessionState(
        session_id=session_id,
        user_id="anonymous",
        phase=3,
        phase_status="in_progress",
    )
    state.chunks = data.get("chunks", [])
    state.embeddings = data.get("embeddings", [])
    state.embedding_meta = data.get("embedding_meta")
    if state.embedding_meta is None:
        state.index_status = "degraded"
    state.upload = UploadResult(
        status="completed",
        progress=1.0,
        total_chunks=data.get("total_chunks", 0),
        indexed_collection=data.get("indexed_collection"),
        message=f"Rehydrated {data.get('total_chunks', 0)} chunks from persistent index",
    )
    _session_store[session_id] = state
    save_session_state(state)
    logger.info("Rehydrated session %s from ChromaDB collection %s", session_id, state.upload.indexed_collection)
    return state


def get_session(session_id: str) -> Optional[SessionState]:
    """Return session state from memory, disk snapshot, or ChromaDB."""
    state = _session_store.get(session_id)
    if state is not None:
        if _apply_training_watchdog(state):
            save_session_state(state)
        return state

    # Try the persisted JSON snapshot first.
    state = load_session_state(session_id)
    if state is not None:
        _session_store[session_id] = state
        logger.info("Restored session %s from disk snapshot", session_id)
        if _apply_training_watchdog(state):
            save_session_state(state)
        return state

    # Snapshot missing; try to rebuild from vector index alone.
    return _rehydrate_from_chroma(session_id)


def update_session(session_id: str, state: SessionState) -> SessionState:
    _session_store[session_id] = state
    save_session_state(state)
    return state
