from datetime import datetime
from fastapi import APIRouter, HTTPException, Body
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

    # Validate features against the live block registry
    valid_features = await map_features(config.features)
    if len(valid_features) != len(config.features):
        invalid = set(config.features) - set(valid_features)
        raise HTTPException(
            status_code=400,
            detail=f"Some features are invalid or unavailable: {sorted(invalid)}"
        )

    # Validate domain against the live store
    domain_manifest = await load_domain_manifest(config.domain)
    if not domain_manifest:
        raise HTTPException(status_code=400, detail="Domain not available")
    if domain_manifest.get("status") != "available":
        raise HTTPException(status_code=400, detail="Selected domain is not available")

    state.config = config
    state.phase = 2  # move to next phase after config
    state.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return state
