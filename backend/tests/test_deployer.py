"""Tests for the deployer's private-repository guard and zip fallback."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import deployer
from app.core.deployer import (
    _assert_no_client_data_staged,
    _push_package_to_branch,
    _verify_repo_private,
    deploy_to_render,
)


class _FakeUrlopenResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_fake_urlopen(payload: dict, status: int = 200):
    def _fake_urlopen(req, timeout=None):
        return _FakeUrlopenResponse(payload, status)

    return _fake_urlopen


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def deploy_env(monkeypatch, tmp_path: Path):
    """Provide a configured deploy environment pointing at a temp package."""
    monkeypatch.setattr(deployer, "DEPLOY_REPO_URL", "https://github.com/owner/deploy-target")
    monkeypatch.setattr(deployer, "GITHUB_TOKEN", "fake-token")
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    return package_dir


def test_verify_repo_private_accepts_private_repo():
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": True, "full_name": "owner/deploy-target"}),
    ):
        ok, reason = _verify_repo_private("https://github.com/owner/deploy-target", "token")
    assert ok is True
    assert reason == ""


def test_verify_repo_private_rejects_public_repo():
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": False, "full_name": "owner/deploy-target"}),
    ):
        ok, reason = _verify_repo_private("https://github.com/owner/deploy-target", "token")
    assert ok is False
    assert "public" in reason.lower()
    assert "owner/deploy-target" in reason


def test_verify_repo_private_rejects_unreachable_repo():
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/owner/missing",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=None,
    )
    with patch("app.core.deployer.urllib.request.urlopen", side_effect=error):
        ok, reason = _verify_repo_private("https://github.com/owner/missing", "token")
    assert ok is False
    assert "unreachable" in reason.lower()


def test_push_package_to_branch_falls_back_when_deploy_repo_url_unset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(deployer, "DEPLOY_REPO_URL", "")
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    branch, reason = _push_package_to_branch("sess-unset", str(package_dir))

    assert branch is None
    assert reason is not None
    assert "DEPLOY_REPO_URL" in reason


def test_push_package_to_branch_aborts_public_repo(deploy_env):
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": False, "full_name": "owner/deploy-target"}),
    ):
        branch, reason = _push_package_to_branch("sess-public", str(deploy_env))

    assert branch is None
    assert "public" in reason.lower()


def test_push_package_to_branch_proceeds_for_private_repo(deploy_env, tmp_path: Path):
    # Simulate a successful clone/commit/push cycle without touching GitHub.
    def _fake_run_git(args, cwd=""):
        if args[0] == "clone":
            # Create the clone directory and a fake deployments tree.
            clone_dir = args[-1]
            Path(clone_dir).mkdir(parents=True, exist_ok=True)
            (Path(clone_dir) / "deployments").mkdir(exist_ok=True)
            return _completed()
        if args[0] == "status" and "--short" in args:
            return _completed("A  deployments/sess-private/Dockerfile\n")
        return _completed()

    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": True, "full_name": "owner/deploy-target"}),
    ), patch("app.core.deployer._run_git", side_effect=_fake_run_git):
        branch, reason = _push_package_to_branch("sess-private", str(deploy_env))

    assert branch == "deploy-sess-private"
    assert reason is None


def test_deploy_to_render_surfaces_public_repo_abort(deploy_env, monkeypatch):
    monkeypatch.setattr(deployer, "RENDER_API_KEY", "render-key")
    monkeypatch.setattr(deployer, "RENDER_OWNER_ID", "owner-id")

    class FakeState:
        config = type("Config", (), {"domain": "medical"})()

    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": False, "full_name": "owner/deploy-target"}),
    ):
        result = deploy_to_render(
            "sess-abort",
            FakeState(),
            str(deploy_env),
            "cerebrumdev-medical-sess",
            {"CEREBRUM_MASTER_KEY": "key"},
        )

    assert result["status"] == "packaged"
    assert "public" in result["message"].lower()
    assert "owner/deploy-target" in result["message"]


def test_assert_no_client_data_staged_raises_when_guard_not_passed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run = lambda args, cwd: subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    _run(["init"], cwd=str(repo))
    _run(["config", "user.email", "test@example.com"], cwd=str(repo))
    _run(["config", "user.name", "Test"], cwd=str(repo))

    docs = repo / "data" / "docs"
    docs.mkdir(parents=True)
    (docs / "file.txt").write_text("secret", encoding="utf-8")
    _run(["add", "."], cwd=str(repo))

    with pytest.raises(RuntimeError, match="Scrub check failed"):
        _assert_no_client_data_staged(str(repo), guard_passed=False)


def test_assert_no_client_data_staged_passes_when_guard_passed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run = lambda args, cwd: subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    _run(["init"], cwd=str(repo))
    _run(["config", "user.email", "test@example.com"], cwd=str(repo))
    _run(["config", "user.name", "Test"], cwd=str(repo))

    docs = repo / "data" / "docs"
    docs.mkdir(parents=True)
    (docs / "file.txt").write_text("secret", encoding="utf-8")
    _run(["add", "."], cwd=str(repo))

    # When the guard has passed, client data may legitimately be staged.
    _assert_no_client_data_staged(str(repo), guard_passed=True)


def test_verify_repo_private_invalid_url():
    ok, reason = _verify_repo_private("https://example.com/not-github", "token")
    assert ok is False
    assert "Not a valid GitHub repository URL" in reason
