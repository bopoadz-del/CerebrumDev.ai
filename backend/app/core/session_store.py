import logging
from typing import Dict, Optional
from ..models.session import SessionState, UploadResult

logger = logging.getLogger(__name__)

_session_store: Dict[str, SessionState] = {}


def create_session(session_id: str, user_id: str) -> SessionState:
    state = SessionState(session_id=session_id, user_id=user_id)
    _session_store[session_id] = state
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
    state.upload = UploadResult(
        status="completed",
        progress=1.0,
        total_chunks=data.get("total_chunks", 0),
        indexed_collection=data.get("indexed_collection"),
        message=f"Rehydrated {data.get('total_chunks', 0)} chunks from persistent index",
    )
    _session_store[session_id] = state
    logger.info("Rehydrated session %s from ChromaDB collection %s", session_id, state.upload.indexed_collection)
    return state


def get_session(session_id: str) -> Optional[SessionState]:
    state = _session_store.get(session_id)
    if state is not None:
        return state
    # Session may have been dropped from memory after a restart; try ChromaDB.
    return _rehydrate_from_chroma(session_id)


def update_session(session_id: str, state: SessionState) -> SessionState:
    _session_store[session_id] = state
    return state
