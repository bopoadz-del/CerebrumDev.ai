"""Together AI fine-tune orchestrator (Phase 4: Tinker)."""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List

import httpx
from fastapi import HTTPException

from ..models.session import TrainingJob
from .session_store import get_session, update_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables (set in .env / Render)
#   TOGETHER_API_KEY   – required Together AI API key
#   TOGETHER_BASE_URL  – optional, defaults to https://api.together.xyz
#   FINE_TUNE_BASE_MODEL – base model to fine-tune (default Qwen 7B)
# ---------------------------------------------------------------------------

TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")
TOGETHER_BASE_URL = os.environ.get("TOGETHER_BASE_URL", "https://api.together.xyz")
FINE_TUNE_BASE_MODEL = os.environ.get(
    "FINE_TUNE_BASE_MODEL",
    os.environ.get("TOGETHER_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
)


def validate_training_data(pairs: List[Dict[str, str]], min_pairs: int = 10) -> bool:
    """Validate that each pair has non-empty 'question' and 'answer'."""
    if not pairs:
        raise ValueError("Training data is empty.")
    if len(pairs) < min_pairs:
        raise ValueError(f"Need at least {min_pairs} Q&A pairs, got {len(pairs)}")
    for i, pair in enumerate(pairs):
        q = (pair.get("question") or "").strip()
        a = (pair.get("answer") or "").strip()
        if not q or not a:
            raise ValueError(f"Pair {i + 1} has empty question or answer")
    return True


def format_jsonl(
    pairs: List[Dict[str, str]],
    system_prompt: str = "You are a helpful assistant.",
) -> str:
    """Convert Q&A pairs to the Together AI chat-completion JSONL format."""
    lines = []
    for p in pairs:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (p.get("question") or "").strip()},
            {"role": "assistant", "content": (p.get("answer") or "").strip()},
        ]
        lines.append(json.dumps({"messages": messages}, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


async def _upload_file(content: str, purpose: str = "fine-tune") -> str:
    """Upload JSONL content to Together AI and return the file id."""
    if not TOGETHER_API_KEY:
        raise ValueError("TOGETHER_API_KEY is not set.")

    url = f"{TOGETHER_BASE_URL.rstrip('/')}/v1/files"
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers=headers,
            files={
                "file": ("training.jsonl", content.encode("utf-8"), "application/jsonl"),
                "purpose": (None, purpose),
            },
        )
        if resp.status_code >= 300:
            logger.error("Together AI file upload failed: %s", resp.text)
            raise HTTPException(status_code=resp.status_code, detail=f"Together AI file upload error: {resp.text}")
        data = resp.json()
        file_id = data.get("id")
        if not file_id:
            raise HTTPException(status_code=500, detail="No file ID returned from Together AI")
        return file_id


async def _start_fine_tune_job(file_id: str, base_model: str, session_id: str) -> str:
    """Start a fine-tune job and return the Together AI job ID."""
    url = f"{TOGETHER_BASE_URL.rstrip('/')}/v1/fine-tuning/jobs"
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": base_model,
        "training_file": file_id,
        "suffix": f"cerebrum-{session_id[:8]}",
        "hyperparameters": {
            "n_epochs": 3,
            "learning_rate_multiplier": 1.0,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 300:
            logger.error("Together AI job creation failed: %s", resp.text)
            raise HTTPException(status_code=resp.status_code, detail=f"Together AI job creation error: {resp.text}")
        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            raise HTTPException(status_code=500, detail="No job ID returned from Together AI")
        return job_id


async def _get_fine_tune_status(job_id: str) -> dict:
    """Poll Together AI for job status."""
    url = f"{TOGETHER_BASE_URL.rstrip('/')}/v1/fine-tuning/jobs/{job_id}"
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code >= 300:
            raise HTTPException(status_code=resp.status_code, detail="Failed to fetch training status from Together AI")
        data = resp.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0.0)
        fine_tuned_model = data.get("fine_tuned_model")
        error = data.get("error") if status == "failed" else None
        return {
            "status": status,
            "progress": progress,
            "fine_tuned_model": fine_tuned_model,
            "error": error,
        }


async def start_training(session_id: str) -> TrainingJob:
    """Validate data, upload to Together AI, and start a fine-tune job."""
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    pairs = state.training_data
    try:
        validate_training_data(pairs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    jsonl_content = format_jsonl(pairs)

    state.training_job = TrainingJob(
        status="preparing",
        dataset_size=len(pairs),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    update_session(session_id, state)

    try:
        file_id = await _upload_file(jsonl_content)
        job_id = await _start_fine_tune_job(file_id, FINE_TUNE_BASE_MODEL, session_id)

        state.training_job.job_id = job_id
        state.training_job.status = "queued"
        state.training_job.updated_at = datetime.utcnow()
        update_session(session_id, state)
        return state.training_job
    except HTTPException:
        state.training_job.status = "failed"
        state.training_job.updated_at = datetime.utcnow()
        update_session(session_id, state)
        raise
    except Exception as exc:
        logger.exception("Failed to start training for %s", session_id)
        state.training_job.status = "failed"
        state.training_job.error = str(exc)
        state.training_job.updated_at = datetime.utcnow()
        update_session(session_id, state)
        raise HTTPException(status_code=500, detail=f"Training start failed: {exc}")


async def get_training_status(session_id: str) -> TrainingJob:
    """Poll Together AI for status and update the session."""
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if not state.training_job.job_id:
        raise HTTPException(status_code=404, detail="No training job found for this session")

    job_id = state.training_job.job_id
    try:
        status_info = await _get_fine_tune_status(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to fetch training status for %s", session_id)
        raise HTTPException(status_code=500, detail=f"Status fetch failed: {exc}")

    status_map = {
        "pending": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "idle",
    }
    mapped_status = status_map.get(status_info["status"], "idle")

    state.training_job.status = mapped_status
    state.training_job.progress = float(status_info.get("progress", 0.0) or 0.0)
    if status_info["status"] == "succeeded":
        state.training_job.fine_tuned_model_id = status_info.get("fine_tuned_model")
    elif status_info["status"] == "failed":
        state.training_job.error = status_info.get("error") or "Unknown error"
    state.training_job.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return state.training_job


async def cancel_training(session_id: str) -> bool:
    """Cancel a running fine-tune job on Together AI."""
    state = get_session(session_id)
    if not state or not state.training_job.job_id:
        return False

    job_id = state.training_job.job_id
    url = f"{TOGETHER_BASE_URL.rstrip('/')}/v1/fine-tuning/jobs/{job_id}/cancel"
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers)
        if resp.status_code == 200:
            state.training_job.status = "idle"
            state.training_job.updated_at = datetime.utcnow()
            update_session(session_id, state)
            return True
    return False
