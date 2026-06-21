import os
import uuid
from pathlib import Path
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException

from ..models.session import SessionState
from ..core.session_store import get_session, update_session
from ..core.upload_processor import process_upload
from ..core.background import start_task

router = APIRouter()

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")


def _session_files_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/{session_id}/upload")
async def upload_files(session_id: str, files: List[UploadFile] = File(...)):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    if state.upload.status == "processing":
        raise HTTPException(status_code=409, detail="Upload already in progress")

    target_dir = _session_files_dir(session_id)
    saved_paths: List[str] = []

    state.upload.status = "pending"
    state.upload.progress = 0.0
    state.upload.failed_files = []
    state.upload.message = "Uploading files..."
    update_session(session_id, state)

    for file in files:
        safe_name = Path(file.filename or "unknown").name
        dest = target_dir / f"{uuid.uuid4().hex}_{safe_name}"
        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)
        saved_paths.append(str(dest))

    # session_id doubles as job_id for simplicity
    start_task(session_id, process_upload(session_id, saved_paths))

    return {"job_id": session_id, "files_received": len(saved_paths)}


@router.get("/{session_id}/upload/status")
async def upload_status(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state.upload


@router.get("/{session_id}/upload/result")
async def upload_result(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if state.upload.status not in ("completed", "completed_with_warnings", "failed"):
        raise HTTPException(status_code=400, detail="Upload not finished")
    return {
        "status": state.upload.status,
        "indexed_collection": state.upload.indexed_collection,
        "total_chunks": state.upload.total_chunks,
        "failed_files": state.upload.failed_files,
        "message": state.upload.message,
    }
