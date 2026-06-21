from datetime import datetime
from fastapi import APIRouter, HTTPException, Body
from ..models.session import SessionConfig
from ..core.session_store import get_session, update_session
from ..core.domain_loader import load_domain_manifest

router = APIRouter()


@router.post("/{session_id}/config")
async def save_config(session_id: str, config: SessionConfig = Body(...)):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate domain against the live store
    domain_manifest = await load_domain_manifest(config.domain)
    if not domain_manifest:
        raise HTTPException(status_code=400, detail="Domain not available")
    if domain_manifest.get("status") != "available":
        raise HTTPException(status_code=400, detail="Selected domain is not available")

    state.config = config
    state.phase = 2  # move to Phase 2 (upload) after config
    state.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return state
