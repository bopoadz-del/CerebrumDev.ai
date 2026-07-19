"""CerebrumDev.ai factory Google Drive integration.

All routes are scoped to a factory session. The OAuth callback is exposed at
``/v1/sessions/drive/callback`` without API-key auth because Google cannot send
our Bearer header; it is protected by the single-use OAuth ``state`` value.

Tokens are encrypted at rest via ``DATA_ENCRYPTION_KEY`` and never returned in
API responses.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.google_drive_connector import (
    AuditEvent,
    DriveConnection,
    DriveConnectionStore,
    DriveFileRecord,
    DriveFolderBinding,
    DriveSyncJob,
    build_auth_url,
    configured,
    exchange_code,
    fetch_email,
    frontend_url,
    get_access_token,
    list_files,
)
from app.core.session_store import get_session

logger = logging.getLogger(__name__)

router = APIRouter()
callback_router = APIRouter()

_store = DriveConnectionStore()
_STATE_TTL = 600
_pending_states: dict[str, tuple[str, str, float]] = {}  # state -> (session_id, user_id, issued_at)


class BindFolderRequest(BaseModel):
    folder_id: str
    folder_name: str = ""


def _prune_states() -> None:
    cutoff = time.time() - _STATE_TTL
    expired = [s for s, (_, _, issued) in _pending_states.items() if issued < cutoff]
    for state in expired:
        del _pending_states[state]


def _require_session(session_id: str) -> dict[str, Any]:
    """Dependency that loads the session or raises a non-leaking 404."""
    state = get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": state.session_id,
        "user_id": state.user_id,
    }


def _active_connection(session_id: str) -> DriveConnection:
    """Return the first active connection for a session, or 409."""
    connections = [c for c in _store.list_connections(session_id) if c.status == "active"]
    if not connections:
        raise HTTPException(status_code=409, detail="Google Drive is not connected.")
    return connections[0]


@router.get("/{session_id}/drive/connect")
async def drive_connect(session: dict = Depends(_require_session)):
    if not configured():
        raise HTTPException(
            status_code=503,
            detail="Google Drive not configured — set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.",
        )
    _prune_states()
    state = secrets.token_urlsafe(24)  # type: ignore[name-defined]
    _pending_states[state] = (session["session_id"], session["user_id"], time.time())
    return {"auth_url": build_auth_url(state)}


@callback_router.get("/v1/sessions/drive/callback")
async def drive_callback(
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
):
    _prune_states()
    pending = _pending_states.pop(state, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    session_id, user_id, _ = pending

    if error:
        return RedirectResponse(f"{frontend_url()}/?drive=error", status_code=302)
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    data = await exchange_code(code)
    access_token = data["access_token"]
    email = await fetch_email(access_token)
    connection_id = uuid.uuid4().hex[:16]

    _store.save_token(
        session_id,
        connection_id,
        {
            "access_token": access_token,
            "refresh_token": data.get("refresh_token", ""),
            "expiry": time.time() + int(data.get("expires_in", 3600)),
            "email": email,
        },
    )
    connection = DriveConnection(
        connection_id=connection_id,
        session_id=session_id,
        user_id=user_id,
        email=email,
        status="active",
    )
    _store.save_connection(connection)
    _store.append_audit_event(
        AuditEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type="drive.connected",
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            details={"email": email},
        )
    )
    return RedirectResponse(f"{frontend_url()}/?drive=connected", status_code=302)


@router.get("/{session_id}/drive/status")
async def drive_status(session: dict = Depends(_require_session)):
    connections = _store.list_connections(session["session_id"])
    active = [c for c in connections if c.status == "active"]
    return {
        "configured": configured(),
        "connected": bool(active),
        "connections": [
            {
                "connection_id": c.connection_id,
                "email": c.email,
                "status": c.status,
                "connected_at": c.connected_at.isoformat(),
            }
            for c in connections
        ],
    }


@router.post("/{session_id}/drive/disconnect")
async def drive_disconnect(session: dict = Depends(_require_session)):
    active = [c for c in _store.list_connections(session["session_id"]) if c.status == "active"]
    if not active:
        raise HTTPException(status_code=409, detail="Google Drive is not connected.")
    for conn in active:
        _store.delete_connection(session["session_id"], conn.connection_id)
        _store.append_audit_event(
            AuditEvent(
                event_id=uuid.uuid4().hex[:16],
                event_type="drive.disconnected",
                session_id=session["session_id"],
                user_id=session["user_id"],
                connection_id=conn.connection_id,
            )
        )
    return {"status": "disconnected"}


@router.get("/{session_id}/drive/files")
async def drive_files(
    folder_id: str = Query(""),
    q: str = Query(""),
    session: dict = Depends(_require_session),
):
    connection = _active_connection(session["session_id"])
    access_token = await get_access_token(
        _store, session["session_id"], connection.connection_id
    )
    try:
        files = await list_files(
            access_token,
            folder_id=folder_id or None,
            q=q,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Drive list failed for session %s", session["session_id"])
        raise HTTPException(status_code=502, detail=f"Drive list failed: {exc}") from exc
    return {"files": files, "folder_id": folder_id or "root"}


@router.post("/{session_id}/drive/bind-folder")
async def drive_bind_folder(
    req: BindFolderRequest,
    session: dict = Depends(_require_session),
):
    connection = _active_connection(session["session_id"])
    binding_id = uuid.uuid4().hex[:16]
    binding = DriveFolderBinding(
        binding_id=binding_id,
        connection_id=connection.connection_id,
        session_id=session["session_id"],
        folder_id=req.folder_id,
        folder_name=req.folder_name or req.folder_id,
    )
    _store.save_binding(binding)
    _store.append_audit_event(
        AuditEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type="drive.folder_bound",
            session_id=session["session_id"],
            user_id=session["user_id"],
            connection_id=connection.connection_id,
            binding_id=binding_id,
            details={"folder_id": req.folder_id, "folder_name": req.folder_name},
        )
    )
    return {"binding_id": binding_id, "folder_id": req.folder_id}


@router.get("/{session_id}/drive/bindings")
async def drive_bindings(session: dict = Depends(_require_session)):
    return {"bindings": [b.model_dump() for b in _store.list_bindings(session["session_id"])]}


async def _run_sync_job(
    session_id: str,
    user_id: str,
    binding_id: str,
    job_id: str,
) -> None:
    """Background sync: list files in the bound folder and record metadata."""
    job = _store.load_job(session_id, job_id)
    if job is None:
        return
    job.status = "running"
    job.updated_at = datetime.datetime.now(datetime.timezone.utc)
    _store.save_job(job)

    try:
        connection = _store.load_connection(session_id, job.connection_id)
        if connection is None:
            raise RuntimeError("Connection not found")
        binding = _store.load_binding(session_id, binding_id)
        if binding is None:
            raise RuntimeError("Binding not found")
        access_token = await get_access_token(_store, session_id, connection.connection_id)
        files = await list_files(access_token, folder_id=binding.folder_id)
        imported = 0
        for f in files:
            # Skip folders and unsupported native Google types for the pilot.
            mime = f.get("mimeType", "")
            if mime == "application/vnd.google-apps.folder":
                continue
            record = DriveFileRecord(
                record_id=uuid.uuid4().hex[:16],
                job_id=job_id,
                binding_id=binding_id,
                session_id=session_id,
                drive_file_id=f["id"],
                name=f.get("name", "unnamed"),
                mime_type=mime,
            )
            _store.save_file_record(record)
            imported += 1
        job.status = "done"
        job.imported_count = imported
    except Exception as exc:  # noqa: BLE001
        logger.exception("Drive sync failed session=%s job=%s", session_id, job_id)
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.failed_count += 1
    job.updated_at = datetime.datetime.now(datetime.timezone.utc)
    _store.save_job(job)
    _store.append_audit_event(
        AuditEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type=(
                "drive.sync.completed" if job.status == "done" else "drive.sync.failed"
            ),
            session_id=session_id,
            user_id=user_id,
            connection_id=job.connection_id,
            binding_id=binding_id,
            details={"imported_count": job.imported_count, "error": job.error},
        )
    )


@router.post("/{session_id}/drive/sync/{binding_id}", status_code=202)
async def drive_sync(
    binding_id: str,
    background_tasks: BackgroundTasks,
    session: dict = Depends(_require_session),
):
    connection = _active_connection(session["session_id"])
    binding = _store.load_binding(session["session_id"], binding_id)
    if binding is None or binding.connection_id != connection.connection_id:
        raise HTTPException(status_code=404, detail="Folder binding not found")
    job_id = uuid.uuid4().hex[:12]
    job = DriveSyncJob(
        job_id=job_id,
        binding_id=binding_id,
        connection_id=connection.connection_id,
        session_id=session["session_id"],
        status="queued",
    )
    _store.save_job(job)
    background_tasks.add_task(
        _run_sync_job,
        session["session_id"],
        session["user_id"],
        binding_id,
        job_id,
    )
    return {
        "status": "queued",
        "job_id": job_id,
        "binding_id": binding_id,
        "status_url": f"/v1/sessions/{session['session_id']}/drive/sync/{job_id}/status",
    }


@router.get("/{session_id}/drive/sync/{job_id}/status")
async def drive_sync_status(
    job_id: str,
    session: dict = Depends(_require_session),
):
    job = _store.load_job(session["session_id"], job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return job.model_dump()
