import uuid
from fastapi import APIRouter, HTTPException
from ..models.session import SessionState
from ..core.session_store import create_session, get_session

router = APIRouter()

@router.post("/", response_model=SessionState)
async def create_new_session():
    # In a real app, user_id comes from auth
    user_id = "anonymous"
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    return create_session(session_id=session_id, user_id=user_id)

@router.get("/{session_id}", response_model=SessionState)
async def get_session_state(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state
