"""Deploy a packaged CerebrumDev session to Render (cloud) or prepare an edge package."""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..models.session import SessionState

logger = logging.getLogger(__name__)

# Matches any userinfo (``user:token@`` or ``token@``) embedded in a URL, so a
# credential echoed by git in stderr/stdout is redacted before it is logged or
# returned to a caller.
_URL_CREDENTIAL_RE = re.compile(r"(https?://)[^/\s@]+@")


def _scrub_git_output(text: Optional[str]) -> str:
    """Redact embedded URL credentials from git subprocess output."""
    if not text:
        return ""
    return _URL_CREDENTIAL_RE.sub(r"\1", text)

RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "")

# Dedicated deploy target repository. This must never be the CerebrumDev.ai
# repository itself. Git-push deploy is disabled when this variable is unset.
DEPLOY_REPO_URL = os.getenv("DEPLOY_REPO_URL", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Paths inside a generated package that may contain client data. They must never
# be staged for a git push unless the target repository has passed the private-
# repo guard.
_CLIENT_DATA_PATHS = {".env", "vectors.json", "data/docs/"}


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).lower().strip("-")[:40]


def _run_git(args: list, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _ensure_git_identity(repo_dir: str) -> bool:
    name = _run_git(["config", "user.name"], cwd=repo_dir).stdout.strip()
    email = _run_git(["config", "user.email"], cwd=repo_dir).stdout.strip()
    if not name:
        _run_git(["config", "user.name", "CerebrumDev Deployer"], cwd=repo_dir)
    if not email:
        _run_git(["config", "user.email", "deploy@cerebrumdev.ai"], repo_dir)
    return True


def _parse_github_repo(repo_url: str) -> Tuple[str, str]:
    """Extract (owner, repo) from a GitHub HTTPS URL."""
    clean = repo_url.rstrip("/").removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    parts = clean.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Not a valid GitHub repository URL: {repo_url}")
    return parts[0], parts[1]


def _auth_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _verify_repo_private(repo_url: str, token: str) -> Tuple[bool, str]:
    """Return (ok, reason) where ok means the repo is confirmed private.

    If the repo is public, unreachable, or the API call fails, ok is False and
    reason names the repo and explains why the push was aborted.
    """
    try:
        owner, repo = _parse_github_repo(repo_url)
    except ValueError as exc:
        return False, str(exc)

    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers=_auth_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            if data.get("private") is True:
                return True, ""
            return False, f"repository {owner}/{repo} is public"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, f"repository {owner}/{repo} is unreachable or does not exist"
        try:
            body = exc.read().decode()
        except Exception:
            body = ""
        return False, f"repository {owner}/{repo} verification failed: HTTP {exc.code} - {body}"
    except Exception as exc:
        return False, f"repository {owner}/{repo} verification failed: {exc}"


def _assert_no_client_data_staged(repo_dir: str, guard_passed: bool) -> None:
    """Packaging-time scrub check.

    Raise if any client-data path is staged for commit while the private-repo
    guard has not passed. This is a fail-safe: the deployer should never stage
    these files unless the target repository has been verified private.
    """
    if guard_passed:
        return
    status = _run_git(["status", "--short"], cwd=repo_dir)
    staged = [line for line in status.stdout.splitlines() if line and line[0] in ("A", "M", "R")]
    for line in staged:
        # status lines look like "AM path/to/file"
        path = line[3:].strip()
        for client_path in _CLIENT_DATA_PATHS:
            if path == client_path or path.startswith(client_path):
                raise RuntimeError(
                    f"Scrub check failed: client data path '{path}' is staged "
                    "but the private-repo guard has not passed."
                )


def _authenticated_repo_url(repo_url: str, token: str) -> str:
    """Return a clone/push URL with the token embedded."""
    if repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://x-access-token:{token}@", 1)
    return repo_url


def _push_package_to_branch(session_id: str, package_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Push the package files to a dedicated branch in DEPLOY_REPO_URL.

    Returns (branch_name, abort_reason). branch_name is None when the push was
    aborted; abort_reason then contains a human-readable explanation that can
    be surfaced in the deploy status.
    """
    if not DEPLOY_REPO_URL:
        logger.info(
            "DEPLOY_REPO_URL is not configured; skipping git-push deploy for %s "
            "and falling back to zip package.",
            session_id,
        )
        return None, "DEPLOY_REPO_URL not configured; using zip package"

    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN is not configured; cannot verify or push to %s", DEPLOY_REPO_URL)
        return None, "GITHUB_TOKEN not configured; cannot verify deploy target"

    is_private, reason = _verify_repo_private(DEPLOY_REPO_URL, GITHUB_TOKEN)
    if not is_private:
        logger.error("Deploy target verification failed: %s", reason)
        return None, f"Deploy target verification failed: {reason}"

    branch = f"deploy-{_safe_name(session_id)}"
    deploy_path = Path("deployments") / session_id

    with tempfile.TemporaryDirectory(prefix="cerebrum-deploy-") as clone_dir:
        clone = _run_git(["clone", "--depth", "1", _authenticated_repo_url(DEPLOY_REPO_URL, GITHUB_TOKEN), clone_dir])
        if clone.returncode != 0:
            clone_err = _scrub_git_output(clone.stderr)
            logger.error("Failed to clone deploy repo %s: %s", DEPLOY_REPO_URL, clone_err)
            return None, f"Failed to clone deploy repo: {clone_err}"

        _ensure_git_identity(clone_dir)
        _run_git(["checkout", "-B", branch], cwd=clone_dir)

        # Clean any previous package for this session
        full_deploy_path = Path(clone_dir) / deploy_path
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

        _run_git(["add", str(deploy_path)], cwd=clone_dir)

        # Fail-safe: the guard passed above, so this assertion must succeed.
        # It is kept as defense-in-depth in case future refactors stage files
        # before verification.
        _assert_no_client_data_staged(clone_dir, guard_passed=True)

        status = _run_git(["status", "--short"], cwd=clone_dir)
        if not status.stdout.strip():
            logger.info("No package changes to push for %s", session_id)
            return branch, None

        commit = _run_git(
            ["commit", "-m", f"deploy({session_id}): generated deployment package"],
            cwd=clone_dir,
        )
        if commit.returncode != 0:
            commit_err = _scrub_git_output(commit.stderr)
            logger.error("Git commit failed: %s", commit_err)
            return None, f"Git commit failed: {commit_err}"

        push = _run_git(
            ["push", "-u", _authenticated_repo_url(DEPLOY_REPO_URL, GITHUB_TOKEN), branch],
            cwd=clone_dir,
        )
        if push.returncode != 0:
            push_err = _scrub_git_output(push.stderr)
            logger.error("Git push failed: %s", push_err)
            return None, f"Git push failed: {push_err}"

        logger.info("Pushed deployment package to branch %s in %s", branch, DEPLOY_REPO_URL)
        return branch, None


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
        "repo": DEPLOY_REPO_URL,
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

    branch, abort_reason = _push_package_to_branch(session_id, package_dir)
    if not branch:
        return {
            "status": "packaged",
            "message": abort_reason or "Package generated. Git-push deploy is unavailable; use the zip package.",
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

Set `CEREBRUM_LLM_API_KEY` to your Kimi API key for AI chat.
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
