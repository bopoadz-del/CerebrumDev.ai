from fastapi import APIRouter, HTTPException, Request
from ..models.session import SessionState
from ..core.session_store import create_session, get_session

router = APIRouter()

@router.post("/", response_model=SessionState)
async def create_new_session(request: Request):
    # In a real app, user_id comes from auth
    user_id = "anonymous"
    return create_session(session_id=request.headers.get("X-Session-ID", "sess_abc123"), user_id=user_id)

@router.get("/{session_id}", response_model=SessionState)
async def get_session_state(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state
