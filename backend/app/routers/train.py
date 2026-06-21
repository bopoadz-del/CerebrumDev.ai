"""Training router – Phase 4 (Tinker)."""

from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from ..core.session_store import get_session, update_session
from ..core.trainer import (
    cancel_training,
    get_training_status,
    start_training,
    validate_training_data,
)
from ..models.session import TrainingJob

router = APIRouter()


@router.post("/{session_id}/train/data")
async def save_training_data(session_id: str, payload: Dict[str, Any]):
    """Store user-provided Q&A pairs on the session."""
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    pairs = payload.get("training_data", [])
    if not isinstance(pairs, list):
        raise HTTPException(status_code=400, detail="training_data must be a list")

    # Normalize keys.
    normalized: List[Dict[str, str]] = []
    for item in pairs:
        q = str(item.get("question") or item.get("q") or "").strip()
        a = str(item.get("answer") or item.get("a") or "").strip()
        if q and a:
            normalized.append({"question": q, "answer": a})

    state.training_data = normalized
    state.training_enabled = payload.get("training_enabled", True)
    state.updated_at = datetime.utcnow()
    update_session(session_id, state)

    return {
        "session_id": session_id,
        "training_data_count": len(normalized),
        "training_enabled": state.training_enabled,
    }


@router.post("/{session_id}/train")
async def trigger_training(session_id: str) -> TrainingJob:
    """Validate training data and start a Cloudflare fine-tune job."""
    return await start_training(session_id)


@router.get("/{session_id}/train/status")
async def training_status(session_id: str) -> TrainingJob:
    """Return the current training job status (polling Cloudflare if needed)."""
    return await get_training_status(session_id)


@router.delete("/{session_id}/train")
async def delete_training(session_id: str):
    """Cancel/clear the training job for this session."""
    success = await cancel_training(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="No training job to cancel")
    return {"status": "cancelled", "session_id": session_id}
