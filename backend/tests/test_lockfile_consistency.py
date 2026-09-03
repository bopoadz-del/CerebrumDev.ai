"""Lockfile-consistency twin: mutation-tested, fail-closed, not automerge-only.

The #244 class is a requirements.txt (or package.json) bump with no lock
delta. dependabot-automerge.yml arms ``gh pr merge --auto`` and must not be
the only gate — a red CI check is what blocks the merge. These tests feed
synthetic diffs; they do not depend on a live dependabot PR.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_lockfile_consistency.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
AUTOMERGE_YML = REPO_ROOT / ".github" / "workflows" / "dependabot-automerge.yml"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_lockfile_consistency", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    return _load_script()


def _run(*args: str, stdin: str | None = None, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir(parents=True)
    (repo / "backend" / "requirements.txt").write_text("foo==1.0.0\n", encoding="utf-8")
    (repo / "backend" / "requirements.lock").write_text(
        "foo==1.0.0\nbar==2.0.0\n", encoding="utf-8"
    )
    (repo / "frontend" / "package.json").write_text('{"name":"x","version":"1.0.0"}\n', encoding="utf-8")
    (repo / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "ci")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


# -- mutation: synthetic path sets (no live dependabot PR) -----------------


def test_a_requirements_txt_bump_without_lock_is_inconsistent(checker):
    findings = checker.inconsistencies(["backend/requirements.txt"])
    assert findings, "txt-only bump must be a finding"
    assert any("requirements.txt" in f and "requirements.lock" in f for f in findings)


def test_b_matching_lock_delta_is_consistent(checker):
    assert checker.inconsistencies(
        ["backend/requirements.txt", "backend/requirements.lock"]
    ) == []


def test_c_lock_only_is_consistent(checker):
    assert checker.inconsistencies(["backend/requirements.lock"]) == []


def test_package_json_without_lock_is_inconsistent(checker):
    findings = checker.inconsistencies(["frontend/package.json"])
    assert findings
    assert any("package.json" in f for f in findings)


def test_package_json_with_package_lock_is_consistent(checker):
    assert checker.inconsistencies(
        ["frontend/package.json", "frontend/package-lock.json"]
    ) == []


def test_package_json_with_yarn_lock_is_consistent(checker):
    assert checker.inconsistencies(
        ["frontend/package.json", "frontend/yarn.lock"]
    ) == []


def test_unrelated_paths_are_consistent(checker):
    assert checker.inconsistencies(["README.md", "backend/app/main.py"]) == []


# -- mutation: CLI exit codes on synthetic --changed diffs -----------------


def test_cli_a_txt_without_lock_exits_1():
    proc = _run("--changed", "backend/requirements.txt")
    assert proc.returncode == 1, proc.stderr
    assert "requirements.lock" in proc.stderr


def test_cli_b_matching_lock_delta_exits_0():
    proc = _run("--changed", "backend/requirements.txt", "backend/requirements.lock")
    assert proc.returncode == 0, proc.stderr
    assert "ok:" in proc.stdout


def test_cli_c_lock_only_exits_0():
    proc = _run("--changed", "backend/requirements.lock")
    assert proc.returncode == 0, proc.stderr


def test_cli_stdin_txt_without_lock_exits_1():
    proc = _run("--stdin", stdin="backend/requirements.txt\n")
    assert proc.returncode == 1, proc.stderr


# -- mutation: synthetic git diffs (the CI --base path) --------------------


def test_git_a_txt_bump_without_lock_exits_1(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "backend" / "requirements.txt").write_text("foo==1.1.0\n", encoding="utf-8")
    _git(repo, "add", "backend/requirements.txt")
    _git(repo, "commit", "-m", "txt only — the #244 class")
    proc = _run("--base", "HEAD~1", cwd=repo)
    assert proc.returncode == 1, proc.stderr
    assert "requirements.lock" in proc.stderr


def test_git_b_matching_lock_delta_exits_0(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "backend" / "requirements.txt").write_text("foo==1.1.0\n", encoding="utf-8")
    (repo / "backend" / "requirements.lock").write_text(
        "foo==1.1.0\nbar==2.0.0\n", encoding="utf-8"
    )
    _git(repo, "add", "backend/requirements.txt", "backend/requirements.lock")
    _git(repo, "commit", "-m", "txt + lock")
    proc = _run("--base", "HEAD~1", cwd=repo)
    assert proc.returncode == 0, proc.stderr


def test_git_c_lock_only_exits_0(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "backend" / "requirements.lock").write_text(
        "foo==1.0.0\nbar==2.0.1\n", encoding="utf-8"
    )
    _git(repo, "add", "backend/requirements.lock")
    _git(repo, "commit", "-m", "lock only")
    proc = _run("--base", "HEAD~1", cwd=repo)
    assert proc.returncode == 0, proc.stderr


def test_git_cannot_determine_diff_fails_closed(tmp_path):
    repo = _init_repo(tmp_path)
    proc = _run("--base", "this-ref-does-not-exist", cwd=repo)
    assert proc.returncode == 1, proc.stdout
    assert "cannot determine changed files" in proc.stderr


# -- wiring: the twin lives in CI, not only in automerge -------------------


def _ci_doc():
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))


def _automerge_doc():
    return yaml.safe_load(AUTOMERGE_YML.read_text(encoding="utf-8"))


def test_ci_has_a_fail_closed_lockfile_job():
    """A dedicated job, not continue-on-error. Deleting it reopens #244."""
    doc = _ci_doc()
    jobs = doc.get("jobs") or {}
    assert "lockfile-consistency" in jobs, "ci.yml must have a lockfile-consistency job"
    job = jobs["lockfile-consistency"]
    assert job.get("continue-on-error") in (None, False)
    steps = job.get("steps") or []
    runs = "\n".join(str(s.get("run") or "") for s in steps)
    assert "check_lockfile_consistency.py" in runs
    assert "--base" in runs
    # A red check is the gate. Do not skip the job, and do not let a step
    # failure be ignored.
    for step in steps:
        assert step.get("continue-on-error") in (None, False)
        assert step.get("if") != "always()"


def test_backend_job_also_runs_the_checker():
    """Backend (pytest) is an existing required check. Fail it too.

    A newly named job is invisible to branch protection until someone adds
    it. Running the same script from the backend job means a #244-class
    bump goes red on a check auto-merge already waits for.
    """
    job = (_ci_doc().get("jobs") or {}).get("backend") or {}
    runs = "\n".join(str(s.get("run") or "") for s in (job.get("steps") or []))
    assert "check_lockfile_consistency.py" in runs


def test_automerge_still_queues_behind_required_checks():
    """Automerge must keep ``--auto``. Skipping CI would re-open #244."""
    checked = 0
    for job in (_automerge_doc().get("jobs") or {}).values():
        for step in job.get("steps") or []:
            for line in str(step.get("run") or "").splitlines():
                code = line.split("#", 1)[0]
                if "gh pr merge" in code:
                    checked += 1
                    assert "--auto" in code
                    assert "--admin" not in code
    assert checked, "automerge lost gh pr merge --auto"


def test_automerge_does_not_inspect_lockfiles():
    """Inspection belongs in CI. pull_request_target must not read the PR tree."""
    for job in (_automerge_doc().get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run_code = "\n".join(
                line.split("#", 1)[0] for line in str(step.get("run") or "").splitlines()
            )
            assert "requirements.lock" not in run_code
            assert "package-lock.json" not in run_code
            assert "check_lockfile_consistency" not in run_code
            assert "actions/checkout" not in str(step.get("uses") or "")
