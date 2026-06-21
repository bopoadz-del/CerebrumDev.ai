from fastapi import APIRouter, HTTPException, Body
from datetime import datetime
from ..models.session import SessionConfig, SessionState
from ..core.session_store import get_session, update_session
from ..core.feature_mapper import map_features
from ..core.domain_loader import load_domain_manifest

router = APIRouter()

@router.post("/{session_id}/config", response_model=SessionState)
async def save_config(session_id: str, config: SessionConfig = Body(...)):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Validate features against registry (optional)
    valid_features = map_features(config.features)
    if len(valid_features) != len(config.features):
        raise HTTPException(status_code=400, detail="Some features are invalid")
    
    # Validate domain
    domain_manifest = load_domain_manifest(config.domain)
    if not domain_manifest:
        raise HTTPException(status_code=400, detail="Domain not available")
    
    state.config = config
    state.phase = 2  # move to next phase after config
    state.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return state
