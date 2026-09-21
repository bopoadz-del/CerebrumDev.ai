"""Resume a build from its cerebrum-builds branch, pasted into the Floor.

The branch IS the build: every run pushes its tree to
``build/sess_<id>-<suffix>`` on cerebrum-builds. Pasting that link (or the
bare ``sess_<id>``) re-attaches the tree as the workspace and continues at the
first phase not yet passed -- never re-running COLLECTOR, CLONER or WRITER
while the branch proves them done.

Phase state comes from the branch itself. The build ledger is Factory-internal
and never ships, so a clean checkout carries none; the tree is the evidence:
handlers in ``app/actions/``, a suite in ``tests/`` and the compiled
``docs/blueprint`` together mean COLLECTOR, CLONER and WRITER finished.

The local attach workspace is keyed by the branch's head sha. Pasting the same
link again while the head has not moved re-enters that workspace and its
ledger, so a run killed mid-TESTER resumes at TESTER -- not from the branch
evidence again, and never earlier.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from app.factory.build.builds_push import (
    BuildsPushError,
    builds_token,
    github_request,
    list_session_build_refs,
    parse_builds_repo,
)

NOT_A_BUILD_LINK = "Not a cerebrum-builds session link"
DERIVED_DONE = ("COLLECTOR", "CLONER", "WRITER")

_TREE_LINK = re.compile(
    r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/tree/(\S+)"
)
_BRANCH = re.compile(r"^build/sess_[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*$")
_BARE = re.compile(r"^\s*(?:build/)?(sess_[0-9A-Za-z]+)(-[0-9A-Za-z]+)?\s*$")


class AttachError(RuntimeError):
    """The link resolved to nothing that can be attached."""


def parse_build_link(text: str, env: Mapping[str, str] | None = None) -> Tuple[Optional[str], Optional[str]]:
    """``(branch, None)``, ``(None, refusal)``, or ``(None, None)`` for "not a link".

    A GitHub tree link to any repo other than the configured cerebrum-builds,
    or one that names no ``build/sess_*`` branch, is refused -- never guessed.
    """
    env = env if env is not None else os.environ
    message = str(text or "").strip()
    link = _TREE_LINK.search(message)
    if link:
        owner, repo, branch = link.group(1), link.group(2), link.group(3).rstrip("/.,)")
        want_owner, want_repo, _ = parse_builds_repo(env)
        if (owner.lower(), repo.lower().removesuffix(".git")) != (want_owner.lower(), want_repo.lower()):
            return None, NOT_A_BUILD_LINK
        if not _BRANCH.match(branch):
            return None, NOT_A_BUILD_LINK
        return branch, None
    bare = _BARE.match(message)
    if bare:
        session, suffix = bare.group(1), bare.group(2) or ""
        if suffix:
            return f"build/{session}{suffix}", None
        return resolve_session_branch(session, env), None
    if "cerebrum-builds" in message and "/tree/" in message:
        return None, NOT_A_BUILD_LINK
    return None, None


def resolve_session_branch(session: str, env: Mapping[str, str]) -> str:
    """The newest ``build/<session>-*`` branch; the only one when there is one."""
    owner, repo, _ = parse_builds_repo(env)
    token = builds_token(env)
    refs = list_session_build_refs(owner, repo, session, token=token)
    if not refs:
        raise AttachError(f"no cerebrum-builds branch for {session}")
    if len(refs) == 1:
        return refs[0][0]
    dated = []
    for branch, sha in refs:
        status, body = github_request("GET", f"/repos/{owner}/{repo}/commits/{sha}", token=token)
        when = ""
        if status < 400 and isinstance(body, dict):
            when = str(((body.get("commit") or {}).get("committer") or {}).get("date") or "")
        dated.append((when, branch))
    return max(dated)[1]


@dataclass
class Attached:
    branch: str
    sha: str
    workspace: Path
    blueprint: Dict[str, Any]
    plan: Optional[Dict[str, Any]]
    reused: bool
    #: Phases the branch proves passed: the tree evidence plus every
    #: ``factory: <PHASE> passed`` checkpoint at the tip of its history.
    passed: tuple = DERIVED_DONE


_CHECKPOINT_MSG = re.compile(r"^factory: ([A-Z_]+) passed$")


def checkpointed_phases(workspace: Path) -> tuple:
    """Phases recorded as passed by checkpoint commits at the branch tip.

    Walks newest to oldest and stops at the first commit that is not a
    checkpoint: any later change to the tree invalidates what it recorded.
    """
    proc = _git(["log", "--format=%s", "-n", "50"], workspace)
    found = []
    for subject in (proc.stdout or "").splitlines():
        m = _CHECKPOINT_MSG.match(subject.strip())
        if not m:
            break
        if m.group(1) not in found:
            found.append(m.group(1))
    return tuple(found)


def _git(args, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env, timeout=600)


def _remote_url(env: Mapping[str, str]) -> str:
    owner, repo, url = parse_builds_repo(env)
    token = builds_token(env)
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git" if token else url + ".git"


def head_sha(branch: str, env: Mapping[str, str]) -> str:
    proc = _git(["ls-remote", _remote_url(env), f"refs/heads/{branch}"], Path.cwd())
    line = (proc.stdout or "").strip().split()
    if proc.returncode != 0 or not line:
        raise AttachError(f"branch {branch} not found on cerebrum-builds")
    return line[0]


def branch_proves_writer_done(tree: Path) -> bool:
    return (
        any((tree / "app" / "actions").glob("*.py"))
        and any((tree / "tests").glob("test_*.py"))
        and (tree / "docs" / "blueprint").is_dir()
    )


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _branch_blueprint(workspace: Path) -> Optional[Dict[str, Any]]:
    """The blueprint the build was compiled from, as a validated dict.

    ``product-dna/product_blueprint.yaml`` is the canonical copy.
    ``docs/blueprint/product_blueprint.json`` carries extra build-time keys the
    model forbids, so it is only a fallback, filtered to the model's fields.
    """
    from app.factory.blueprint import ProductBlueprint, load_blueprint

    yaml_path = workspace / "product-dna" / "product_blueprint.yaml"
    if yaml_path.is_file():
        try:
            return load_blueprint(yaml_path).model_dump(mode="json")
        except Exception:  # noqa: BLE001 -- fall through to the JSON copy
            pass
    raw = _read_json(workspace / "docs" / "blueprint" / "product_blueprint.json")
    if not raw:
        return None
    known = set(ProductBlueprint.model_fields)
    try:
        return ProductBlueprint.model_validate({k: v for k, v in raw.items() if k in known}).model_dump(
            mode="json"
        )
    except Exception:  # noqa: BLE001
        return None


def attach(branch: str, parent: Path, env: Mapping[str, str] | None = None) -> Attached:
    """Clone (or re-enter) ``branch`` under ``parent``; return what was attached."""
    env = env if env is not None else os.environ
    if not _BRANCH.match(branch or ""):
        raise AttachError(NOT_A_BUILD_LINK)
    sha = head_sha(branch, env)
    slug = branch.split("/", 1)[1]
    # Absolute: git runs with the parent as cwd, so a relative path would be
    # resolved twice.
    workspace = (Path(parent) / f"{slug}@{sha[:7]}").resolve()
    reused = (workspace / ".git").is_dir() or (workspace / "build_ledger.jsonl").is_file()
    if not reused:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        proc = _git(["clone", "--quiet", "--branch", branch, _remote_url(env), str(workspace)], workspace.parent)
        if proc.returncode != 0:
            raise AttachError(f"could not clone {branch}: {(proc.stderr or '').strip()[-300:]}")
        got = (_git(["rev-parse", "HEAD"], workspace).stdout or "").strip()
        if got != sha:
            raise AttachError(f"{branch} moved during clone ({sha[:7]} -> {got[:7]})")
    blueprint = _branch_blueprint(workspace)
    if not blueprint:
        raise AttachError(f"{branch} carries no readable product blueprint")
    plan = _read_json(workspace / "product-dna" / "capability_resolution.json")
    if not branch_proves_writer_done(workspace):
        raise AttachError(
            f"{branch} does not carry a finished WRITER pass (app/actions, tests, docs/blueprint)"
        )
    passed = tuple(dict.fromkeys(DERIVED_DONE + checkpointed_phases(workspace)))
    return Attached(branch=branch, sha=sha, workspace=workspace, blueprint=blueprint, plan=plan,
                    reused=reused, passed=passed)


def checkpoint(workspace: Path, branch: str, message: str, env: Mapping[str, str] | None = None) -> str:
    """Commit the exportable tree onto ``branch`` and push it. Returns the sha.

    Phase-forward only: the branch is fetched and the workspace is laid over
    it, so nothing on the branch is rewritten. Factory-internal files never
    ship (builds_push.is_exported).
    """
    import tempfile

    from app.factory.build.builds_push import GIT_EMAIL, GIT_NAME, _sync_workspace_onto_tree

    env = env if env is not None else os.environ
    if not builds_token(env):
        raise BuildsPushError("CEREBRUM_BUILDS_GITHUB_TOKEN missing -- cannot checkpoint")
    tmp = Path(tempfile.mkdtemp(prefix="cerebrum-checkpoint-"))
    try:
        proc = _git(["clone", "--quiet", "--depth=1", "--branch", branch, _remote_url(env), "."], tmp)
        if proc.returncode != 0:
            raise BuildsPushError(f"checkpoint clone failed: {(proc.stderr or '')[-300:]}")
        _sync_workspace_onto_tree(Path(workspace), tmp)
        _git(["add", "-A"], tmp)
        _git(["-c", f"user.email={GIT_EMAIL}", "-c", f"user.name={GIT_NAME}",
              "commit", "--allow-empty", "-q", "-m", message], tmp)
        pushed = _git(["push", "-q", "origin", f"HEAD:{branch}"], tmp)
        if pushed.returncode != 0:
            raise BuildsPushError(f"checkpoint push failed: {(pushed.stderr or '')[-300:]}")
        return (_git(["rev-parse", "HEAD"], tmp).stdout or "").strip()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
