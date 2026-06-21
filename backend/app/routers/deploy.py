"""Deployment router – Phase 5 (Ship)."""

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from ..core.session_store import get_session, update_session
from ..core.packager import package_session
from ..core.deployer import deploy_to_render, poll_deploy_status, generate_edge_package
from ..models.session import SessionState

router = APIRouter()


class DeployTarget:
    CLOUD = "cloud"
    EDGE = "edge"


def _update_deployment(state: SessionState, **kwargs):
    for key, value in kwargs.items():
        setattr(state.deployment, key, value)
    state.updated_at = datetime.utcnow()
    update_session(state.session_id, state)


@router.post("/{session_id}/deploy")
async def start_deploy(session_id: str, target: str = "cloud", background_tasks: BackgroundTasks = None):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    if not state.chain_approved or not state.proposed_chain:
        raise HTTPException(status_code=400, detail="Chain must be approved before deployment")

    if target not in (DeployTarget.CLOUD, DeployTarget.EDGE):
        raise HTTPException(status_code=400, detail="Target must be 'cloud' or 'edge'")

    _update_deployment(
        state,
        status="packaging",
        target=target,
        progress=0.1,
        message="Creating deployment package...",
    )

    try:
        package_info = package_session(state)
    except Exception as exc:
        _update_deployment(state, status="failed", progress=0.0, message=f"Packaging failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Packaging failed: {exc}")

    _update_deployment(
        state,
        status="packaged" if target == DeployTarget.EDGE else "deploying",
        progress=0.5,
        message="Package ready",
        package_path=package_info["zip_path"],
        api_key=package_info["api_key"],
    )

    if target == DeployTarget.EDGE:
        try:
            edge_zip = generate_edge_package(state, package_info["package_dir"])
            _update_deployment(state, status="packaged", progress=1.0, message="Edge package ready", package_path=edge_zip)
            return {
                "status": "packaged",
                "download_url": f"/v1/sessions/{session_id}/deploy/package?variant=edge",
                "message": "Edge package generated",
            }
        except Exception as exc:
            _update_deployment(state, status="failed", message=f"Edge packaging failed: {exc}")
            raise HTTPException(status_code=500, detail=f"Edge packaging failed: {exc}")

    # Cloud deployment
    deploy_info = deploy_to_render(
        session_id,
        state,
        package_info["package_dir"],
        package_info["service_name"],
        package_info["env_vars"],
    )

    if deploy_info.get("status") == "packaged":
        _update_deployment(
            state,
            status="packaged",
            progress=1.0,
            message=deploy_info.get("message", "Package ready for manual deploy"),
        )
        return {
            "status": "packaged",
            "download_url": f"/v1/sessions/{session_id}/deploy/package",
            "message": deploy_info.get("message"),
        }

    if deploy_info.get("status") == "failed":
        _update_deployment(
            state,
            status="failed",
            progress=0.0,
            message=deploy_info.get("message", "Deployment failed"),
        )
        return {
            "status": "failed",
            "download_url": f"/v1/sessions/{session_id}/deploy/package",
            "message": deploy_info.get("message"),
        }

    _update_deployment(
        state,
        status=deploy_info.get("status", "deploying"),
        progress=0.75,
        url=deploy_info.get("url"),
        service_id=deploy_info.get("service_id"),
        deploy_id=deploy_info.get("deploy_id"),
        message=deploy_info.get("message"),
    )

    return {
        "status": state.deployment.status,
        "service_id": deploy_info.get("service_id"),
        "service_name": deploy_info.get("service_name"),
        "url": deploy_info.get("url"),
        "dashboard_url": deploy_info.get("dashboard_url"),
        "api_key": package_info["api_key"],
        "download_url": f"/v1/sessions/{session_id}/deploy/package",
        "message": deploy_info.get("message"),
    }


@router.get("/{session_id}/deploy/status")
async def deploy_status(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    deployment = state.deployment
    service_id: Optional[str] = None

    # If the deployment record includes a service_id, poll Render for the latest status.
    # We store service_id on the deployment object when available.
    if getattr(deployment, "service_id", None):
        service_id = deployment.service_id
        latest = poll_deploy_status(service_id)
        status = latest.get("status", deployment.status)
        if status == "live" and deployment.status != "live":
            _update_deployment(state, status="live", progress=1.0, message="Deployment is live")
        elif status in ("build_in_progress", "update_in_progress", "deploying"):
            _update_deployment(state, status="deploying", progress=0.85, message=latest.get("message"))
        elif status == "failed":
            _update_deployment(state, status="failed", message=latest.get("message"))

    return {
        "status": deployment.status,
        "target": deployment.target,
        "progress": deployment.progress,
        "url": deployment.url,
        "api_key": deployment.api_key,
        "message": deployment.message,
        "package_path": deployment.package_path,
    }


@router.get("/{session_id}/deploy/package")
async def download_package(session_id: str, variant: str = "cloud"):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    package_path = state.deployment.package_path
    if not package_path or not os.path.exists(package_path):
        # Try to locate the package on disk
        base = os.path.join(os.getenv("STORAGE_PATH", "./storage"), "sessions", session_id, "deploy")
        candidate = os.path.join(base, f"{variant}.zip")
        if not os.path.exists(candidate):
            candidate = os.path.join(base, "package.zip")
        package_path = candidate

    if not package_path or not os.path.exists(package_path):
        raise HTTPException(status_code=404, detail="Package not found. Run deploy first.")

    filename = f"cerebrumdev-{state.config.domain}-{variant}.zip"
    return FileResponse(package_path, filename=filename, media_type="application/zip")
