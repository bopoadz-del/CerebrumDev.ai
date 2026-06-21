"""Deploy a packaged CerebrumDev session to Render (cloud) or prepare an edge package."""

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..models.session import SessionState

logger = logging.getLogger(__name__)

RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "")
REPO = os.getenv("DEPLOY_REPO", "https://github.com/bopoadz-del/CerebrumDev.ai")
REPO_DIR = os.getenv("DEPLOY_REPO_DIR", str(Path(__file__).parent.parent.parent.parent))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "bopoadz-del")


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).lower().strip("-")[:40]


def _run_git(args: list, cwd: str = REPO_DIR) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _ensure_git_identity() -> bool:
    name = _run_git(["config", "user.name"]).stdout.strip()
    email = _run_git(["config", "user.email"]).stdout.strip()
    if not name:
        _run_git(["config", "user.name", "CerebrumDev Deployer"])
    if not email:
        _run_git(["config", "user.email", "deploy@cerebrumdev.ai"])
    return True


def _push_package_to_branch(session_id: str, package_dir: str) -> Optional[str]:
    """Push the package files to a dedicated branch in the repo.

    Returns the branch name on success, None on failure.
    """
    if not (Path(REPO_DIR) / ".git").exists():
        logger.error("No git repo found at %s", REPO_DIR)
        return None

    _ensure_git_identity()
    branch = f"deploy-{_safe_name(session_id)}"
    deploy_path = Path("deployments") / session_id

    # Make sure we start from the current branch (assumes backend code is committed/pushed or will be)
    _run_git(["checkout", "-B", branch])

    # Clean any previous package for this session
    full_deploy_path = Path(REPO_DIR) / deploy_path
    if full_deploy_path.exists():
        shutil.rmtree(full_deploy_path)
    full_deploy_path.mkdir(parents=True, exist_ok=True)

    # Copy package files into the repo subdir
    for item in Path(package_dir).iterdir():
        dest = full_deploy_path / item.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    _run_git(["add", str(deploy_path)])
    status = _run_git(["status", "--short"])
    if not status.stdout.strip():
        logger.info("No package changes to push for %s", session_id)
        return branch

    commit = _run_git(["commit", "-m", f"deploy({session_id}): generated deployment package"])
    if commit.returncode != 0:
        logger.error("Git commit failed: %s", commit.stderr)
        return None

    # Use the GitHub token for authenticated pushes when running in Render.
    if GITHUB_TOKEN:
        push_url = REPO.replace("https://github.com", f"https://x-access-token:{GITHUB_TOKEN}@github.com")
        push = _run_git(["push", "-u", push_url, branch])
    else:
        push = _run_git(["push", "-u", "origin", branch])
    if push.returncode != 0:
        logger.error("Git push failed: %s", push.stderr)
        return None

    logger.info("Pushed deployment package to branch %s", branch)
    return branch


def _render_request(path: str, payload: Optional[Dict[str, Any]] = None, method: str = "GET") -> Dict[str, Any]:
    url = f"https://api.render.com/v1{path}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        logger.error("Render API HTTP %s: %s", e.code, e.read().decode())
        raise


def _build_service_payload(service_name: str, branch: str, root_dir: str, env_vars: Dict[str, str]) -> Dict[str, Any]:
    return {
        "type": "web_service",
        "name": service_name,
        "ownerId": RENDER_OWNER_ID,
        "repo": REPO,
        "branch": branch,
        "rootDir": root_dir,
        "autoDeploy": "yes",
        "serviceDetails": {
            "env": "docker",
            "plan": "starter",
            "region": "oregon",
            "envSpecificDetails": {"dockerfilePath": "./Dockerfile"},
            "healthCheckPath": "/health",
            "numInstances": 1,
        },
        "envVars": [{"key": k, "value": v} for k, v in env_vars.items()],
    }


def deploy_to_render(
    session_id: str,
    state: SessionState,
    package_dir: str,
    service_name: str,
    env_vars: Dict[str, str],
) -> Dict[str, Any]:
    """Push the package to a branch and create a Render service.

    Returns service metadata, including the dashboard URL and inferred live URL.
    """
    if not RENDER_API_KEY or not RENDER_OWNER_ID:
        return {
            "status": "packaged",
            "message": "RENDER_API_KEY or RENDER_OWNER_ID not configured; manual deploy required",
        }

    branch = _push_package_to_branch(session_id, package_dir)
    if not branch:
        return {
            "status": "packaged",
            "message": "Package generated. Set GITHUB_TOKEN (or push the package branch manually) to enable Render auto-deploy.",
        }

    root_dir = f"deployments/{session_id}"
    payload = _build_service_payload(service_name, branch, root_dir, env_vars)
    try:
        service = _render_request("/services", payload, "POST")
    except Exception as exc:
        logger.exception("Render service creation failed")
        return {"status": "failed", "message": f"Render API error: {exc}"}

    service_obj = service.get("service", service)
    service_id = service_obj.get("id")
    deploy_id = service.get("deployId")
    dashboard_url = service_obj.get("dashboardUrl")
    service_details = service_obj.get("serviceDetails", {})
    url = service_details.get("url") or f"https://{service_name}.onrender.com"

    return {
        "status": "deploying",
        "service_id": service_id,
        "deploy_id": deploy_id,
        "service_name": service_name,
        "branch": branch,
        "url": url,
        "dashboard_url": dashboard_url,
        "message": "Render service created; build in progress",
    }


def poll_deploy_status(service_id: str) -> Dict[str, Any]:
    """Poll Render for the latest deploy status."""
    try:
        deploys = _render_request(f"/services/{service_id}/deploys")
        if not deploys:
            return {"status": "unknown", "message": "No deploys found"}
        latest = deploys[0]
        return {
            "status": latest.get("status", "unknown"),
            "deploy_id": latest.get("id"),
            "message": f"Render deploy status: {latest.get('status')}",
        }
    except Exception as exc:
        logger.exception("Failed to poll Render deploy status")
        return {"status": "unknown", "message": f"Poll failed: {exc}"}


def generate_edge_package(state: SessionState, package_dir: str) -> str:
    """Create a self-contained edge package (zip) and return its path."""
    edge_dir = Path(package_dir).parent / "edge"
    if edge_dir.exists():
        shutil.rmtree(edge_dir)
    shutil.copytree(package_dir, edge_dir)

    # Add an edge-specific README
    readme = edge_dir / "EDGE_README.md"
    readme.write_text(
        f"""# Edge Package — {state.config.domain}

This is a self-contained deployment package for edge/on-premise installation.

## Run locally

```bash
docker build -t cerebrumdev-{state.session_id} .
docker run -p 8000:8000 cerebrumdev-{state.session_id}
```

## Endpoints

- `GET /health`
- `GET /v1/chain`
- `POST /v1/execute`
- `POST /v1/chat`

Set `OLLAMA_URL` to point to your local Ollama instance for AI chat.
""",
        encoding="utf-8",
    )

    zip_path = edge_dir.parent / "edge.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in edge_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(edge_dir))
    return str(zip_path)
