from typing import Dict, Optional
from ..models.session import SessionState

_session_store: Dict[str, SessionState] = {}

def create_session(session_id: str, user_id: str) -> SessionState:
    state = SessionState(session_id=session_id, user_id=user_id)
    _session_store[session_id] = state
    return state

def get_session(session_id: str) -> Optional[SessionState]:
    return _session_store.get(session_id)

def update_session(session_id: str, state: SessionState) -> SessionState:
    _session_store[session_id] = state
    return state
