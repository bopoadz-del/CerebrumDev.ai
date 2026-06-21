"""Cloudflare Workers AI fine-tune orchestrator (Phase 4: Tinker).

This module assumes Cloudflare's training API accepts a public dataset URL.
If the endpoint shape changes, only the payload in `_create_fine_tune_job`
needs to be updated.
"""

import base64
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from ..models.session import SessionState, TrainingJob
from .session_store import get_session, update_session

logger = logging.getLogger(__name__)

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "bopoadz-del")
DEPLOY_REPO = os.getenv("DEPLOY_REPO", "https://github.com/bopoadz-del/CerebrumDev.ai")

# Optional R2 upload (preferred when credentials are provided).
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "")

# Base model for the fine-tune job.  Cloudflare's actual supported models may
# differ; override via FINE_TUNE_BASE_MODEL if needed.
FINE_TUNE_BASE_MODEL = os.getenv(
    "FINE_TUNE_BASE_MODEL",
    os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
)


def _repo_owner_and_name() -> tuple:
    """Extract owner/repo from DEPLOY_REPO."""
    repo = DEPLOY_REPO.rstrip("/").replace("https://github.com/", "").replace(".git", "")
    parts = repo.split("/")
    return parts[0] if len(parts) > 0 else GITHUB_USERNAME, parts[1] if len(parts) > 1 else "CerebrumDev.ai"


def validate_training_data(pairs: List[Dict[str, str]], min_pairs: int = 10) -> bool:
    """Validate that every pair has non-empty question/answer strings."""
    if len(pairs) < min_pairs:
        raise ValueError(f"Need at least {min_pairs} Q&A pairs, got {len(pairs)}")
    for i, pair in enumerate(pairs):
        q = (pair.get("question") or "").strip()
        a = (pair.get("answer") or "").strip()
        if not q or not a:
            raise ValueError(f"Pair {i + 1} has an empty question or answer")
    return True


def format_jsonl(
    pairs: List[Dict[str, str]],
    system_prompt: str = "You are a helpful assistant.",
) -> str:
    """Convert Q&A pairs to the Cloudflare chat-completion JSONL format."""
    lines: List[str] = []
    for p in pairs:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (p.get("question") or "").strip()},
            {"role": "assistant", "content": (p.get("answer") or "").strip()},
        ]
        lines.append(json.dumps({"messages": messages}, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def _upload_to_r2(key: str, content: str) -> Optional[str]:
    """Upload content to Cloudflare R2 if credentials are configured."""
    if not all([R2_BUCKET_NAME, R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT]):
        return None
    try:
        import boto3
        session = boto3.Session(
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
        )
        s3 = session.client("s3", endpoint_url=R2_ENDPOINT)
        s3.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="application/jsonl",
        )
        host = R2_ENDPOINT.replace("https://", "").rstrip("/")
        return f"https://{R2_BUCKET_NAME}.{host}/{key}"
    except Exception as exc:
        logger.warning("R2 upload failed: %s", exc)
        return None


def _github_api_request(path: str, method: str = "GET", data: Optional[bytes] = None) -> Dict[str, Any]:
    """Call the GitHub REST API with the configured GITHUB_TOKEN."""
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error("GitHub API error %s %s: %s", method, path, body)
        raise RuntimeError(f"GitHub API {exc.code}: {body[:200]}") from exc


def _upload_to_github_raw(session_id: str, content: str) -> str:
    """Push the dataset to a dedicated branch/file on GitHub and return the raw URL."""
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set; cannot upload training dataset")

    owner, repo = _repo_owner_and_name()
    branch = "training-data"
    path = f"training/{session_id}.jsonl"
    encoded_content = base64.b64encode(content.encode("utf-8")).decode()

    # Ensure branch exists.
    try:
        _github_api_request(f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
    except RuntimeError:
        # Create branch from default branch.
        default = _github_api_request(f"/repos/{owner}/{repo}")
        default_branch = default.get("default_branch", "master")
        ref = _github_api_request(f"/repos/{owner}/{repo}/git/ref/heads/{default_branch}")
        sha = ref["object"]["sha"]
        _github_api_request(
            f"/repos/{owner}/{repo}/git/refs",
            method="POST",
            data=json.dumps({"ref": f"refs/heads/{branch}", "sha": sha}).encode(),
        )

    # Get current file SHA if it exists.
    file_sha = None
    try:
        existing = _github_api_request(f"/repos/{owner}/{repo}/contents/{path}?ref={branch}")
        file_sha = existing.get("sha")
    except RuntimeError:
        pass

    payload = {
        "message": f"Add training data for session {session_id}",
        "content": encoded_content,
        "branch": branch,
    }
    if file_sha:
        payload["sha"] = file_sha

    _github_api_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        method="PUT",
        data=json.dumps(payload).encode(),
    )

    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"


def upload_dataset(session_id: str, content: str) -> str:
    """Upload the JSONL dataset somewhere publicly reachable.

    Prefers R2 when credentials are available; otherwise falls back to a raw
    GitHub URL so Cloudflare can fetch the training file.
    """
    key = f"training_data/{session_id}.jsonl"
    url = _upload_to_r2(key, content)
    if url:
        logger.info("Uploaded dataset to R2 for %s", session_id)
        return url
    url = _upload_to_github_raw(session_id, content)
    logger.info("Uploaded dataset to GitHub raw URL for %s", session_id)
    return url


async def _create_fine_tune_job(
    session_id: str,
    dataset_url: str,
) -> str:
    """Call Cloudflare's fine-tunes endpoint and return the job ID."""
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be set")

    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/finetunes"
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FINE_TUNE_BASE_MODEL,
        "training_file": dataset_url,
        "name": f"cerebrumdev-{session_id[:20]}",
        "description": f"Fine-tune for session {session_id}",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json()
        if resp.status_code >= 300 or not data.get("success"):
            detail = data.get("errors") or data.get("message") or resp.text
            raise RuntimeError(f"Cloudflare fine-tune creation failed: {detail}")
        result = data.get("result", {})
        job_id = result.get("id") or result.get("finetune_id")
        if not job_id:
            raise RuntimeError(f"Cloudflare response did not contain a job ID: {result}")
        return str(job_id)


async def start_training(session_id: str) -> TrainingJob:
    """Validate data, upload dataset, and start a Cloudflare fine-tune job."""
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    if not state.training_data:
        raise HTTPException(status_code=400, detail="No training_data found for this session")

    try:
        validate_training_data(state.training_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Reset/update job record.
    state.training_job = TrainingJob(
        status="preparing",
        dataset_size=len(state.training_data),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    update_session(session_id, state)

    try:
        jsonl_content = format_jsonl(state.training_data)
        dataset_url = upload_dataset(session_id, jsonl_content)
        state.training_job.dataset_url = dataset_url
        state.training_job.status = "queued"
        update_session(session_id, state)

        job_id = await _create_fine_tune_job(session_id, dataset_url)
        state.training_job.job_id = job_id
        state.training_job.status = "queued"
        state.training_job.updated_at = datetime.utcnow()
        update_session(session_id, state)
        return state.training_job
    except Exception as exc:
        logger.exception("Failed to start training for %s", session_id)
        state.training_job.status = "failed"
        state.training_job.error = str(exc)
        state.training_job.updated_at = datetime.utcnow()
        update_session(session_id, state)
        raise HTTPException(status_code=500, detail=f"Training start failed: {exc}")


def _map_cf_status(status: Optional[str]) -> str:
    if not status:
        return "idle"
    mapping = {
        "pending": "queued",
        "queued": "queued",
        "running": "running",
        "completed": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "failed",
    }
    return mapping.get(status.lower(), "running")


async def get_training_status(session_id: str) -> TrainingJob:
    """Poll Cloudflare for the latest job status and update the session."""
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    job = state.training_job
    if not job.job_id:
        raise HTTPException(status_code=404, detail="No training job found for this session")

    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Cloudflare credentials are not configured")

    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/finetunes/{job.job_id}"
    headers = {"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            data = resp.json()
            if resp.status_code >= 300 or not data.get("success"):
                detail = data.get("errors") or data.get("message") or resp.text
                raise RuntimeError(f"Cloudflare status fetch failed: {detail}")

            result = data.get("result", {})
            job.status = _map_cf_status(result.get("status"))
            job.progress = float(result.get("progress", 0.0) or 0.0)
            job.updated_at = datetime.utcnow()

            if job.status == "succeeded":
                job.fine_tuned_model_id = result.get("model_id") or result.get("fine_tuned_model")
            elif job.status == "failed":
                job.error = result.get("error") or result.get("errors") or "Unknown training error"

            update_session(session_id, state)
            return job
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to fetch training status for %s", session_id)
        raise HTTPException(status_code=500, detail=f"Status fetch failed: {exc}")


async def cancel_training(session_id: str) -> bool:
    """Attempt to cancel the fine-tune job; always clears the local job record."""
    state = get_session(session_id)
    if not state or not state.training_job.job_id:
        return False

    job_id = state.training_job.job_id
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/finetunes/{job_id}"
        headers = {"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.delete(url, headers=headers)
        except Exception as exc:
            logger.warning("Cloudflare cancel request failed (non-fatal): %s", exc)

    state.training_job.status = "idle"
    state.training_job.progress = 0.0
    state.training_job.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return True
