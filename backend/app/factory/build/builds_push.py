"""Push a Factory workspace to private cerebrum-builds (N1a).

Does not reimplement CLONER — copies whatever COLLECTOR/CLONER already
left in ``workspace`` (plus ``docs/coder_brief.md``). Live git+HTTPS
uses ``CEREBRUM_BUILDS_GITHUB_TOKEN``. Collect uses the GitHub API.

Scratch ``build/**`` branches are cut from the store's ``main`` so
``.github/workflows/store-gate.yml`` is on the pushed commit (Actions
``on.push`` reads the workflow from that commit).

TODO(N1c): secret scan of the pushed tree.
TODO(N1c): delete the scratch branch after collect.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.factory.build.cli_receipt import RECEIPT_NAMES

BUILDS_TOKEN_ENV = "CEREBRUM_BUILDS_GITHUB_TOKEN"
BUILDS_REPO_ENV = "CEREBRUM_BUILDS_REPO"
DEFAULT_BUILDS_REPO = "bopoadz-del/cerebrum-builds"
GITHUB_API = "https://api.github.com"
# Compare 404 is usually missing head/seed after BA rename or GitHub ref lag —
# not an API outage. Retry briefly, then fail closed with a clear diagnosis.
COMPARE_404_RETRIES = 5
COMPARE_404_SLEEP_S = 2.0
GIT_NAME = "cerebrum-factory"
GIT_EMAIL = "factory@cerebrum.dev"

_URL_CREDENTIAL_RE = re.compile(r"(https?://)[^/\s@]+@")
_SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class BuildsPushError(RuntimeError):
    """Token/repo missing or push/collect failed before a usable branch tip."""


@dataclass(frozen=True)
class BuildsRef:
    """Scratch branch on cerebrum-builds.

    ``seed_sha`` is the factory seed commit (pre-agent tip). That commit
    is cut from ``cerebrum-builds`` ``main``, so GitHub compare against
    ``main`` or this SHA has a common ancestor. ``collect_branch`` diffs
    agent commits against this SHA (not ``main``) so the factory overlay
    is not judged by the N1b path jail.
    """

    owner: str
    repo: str
    repository_url: str
    branch: str
    seed_sha: str


def builds_token(env: Mapping[str, str]) -> str:
    return str(env.get(BUILDS_TOKEN_ENV) or "").strip()


def sanitize_session_id(raw: str) -> str:
    text = _SESSION_SAFE_RE.sub("-", str(raw or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return (text or "session")[:64]


def make_branch_name(session_id: str, *, suffix: Optional[str] = None) -> str:
    """One ``build/**`` branch per session run (Store gate later triggers here)."""
    sid = sanitize_session_id(session_id)
    tag = (suffix or uuid.uuid4().hex[:8]).strip() or uuid.uuid4().hex[:8]
    return f"build/{sid}-{tag}"


def parse_builds_repo(env: Mapping[str, str]) -> Tuple[str, str, str]:
    raw = str(env.get(BUILDS_REPO_ENV) or "").strip() or DEFAULT_BUILDS_REPO
    if raw.startswith("https://") or raw.startswith("http://"):
        clean = raw.rstrip("/").removesuffix(".git")
        parts = [p for p in clean.split("/") if p]
        if len(parts) < 2:
            raise BuildsPushError(
                f"{BUILDS_REPO_ENV} is not a GitHub owner/repo URL"
            )
        owner, name = parts[-2], parts[-1]
    else:
        if raw.count("/") != 1:
            raise BuildsPushError(
                f"{BUILDS_REPO_ENV} must be owner/repo or a GitHub HTTPS URL"
            )
        owner, name = (p.strip() for p in raw.split("/", 1))
        name = name.removesuffix(".git")
    if not owner or not name or "/" in name:
        raise BuildsPushError(f"{BUILDS_REPO_ENV} missing or invalid")
    return owner, name, f"https://github.com/{owner}/{name}"


def _scrub(text: Optional[str], token: str = "") -> str:
    blob = text or ""
    blob = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", blob)
    if token:
        blob = blob.replace(token, "<redacted>")
    return blob


def _default_run_git(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *list(args)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


def _sync_workspace_onto_tree(src: Path, dest: Path) -> None:
    """Overlay product files onto a cloned ``main`` tree.

    Replaces or adds workspace entries. Keeps ``dest/.github/`` (Store gate)
    even when the workspace also has a ``.github/`` directory.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        raise BuildsPushError(f"workspace is not a directory: {src}")
    for item in src.iterdir():
        if item.name in {".git", ".github"}:
            continue
        target = dest / item.name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(".git"))
        else:
            shutil.copy2(item, target)


def push_workspace(
    workspace: Path,
    *,
    env: Mapping[str, str],
    session_id: str,
    run_git: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    suffix: Optional[str] = None,
) -> BuildsRef:
    """Commit ``workspace`` onto a ``build/**`` branch cut from ``main``."""
    token = builds_token(env)
    if not token:
        raise BuildsPushError(
            f"{BUILDS_TOKEN_ENV} missing — fail-closed; no agent start"
        )
    owner, name, repo_url = parse_builds_repo(env)
    branch = make_branch_name(session_id, suffix=suffix)
    git = run_git or _default_run_git
    tmp = Path(tempfile.mkdtemp(prefix="cerebrum-builds-"))
    try:
        auth_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        _require_git(
            git,
            ["clone", "--depth=1", "--branch", "main", auth_url, "."],
            cwd=tmp,
            token=token,
        )
        parent_proc = _require_git(git, ["rev-parse", "HEAD"], cwd=tmp, token=token)
        parent_sha = (parent_proc.stdout or "").strip()
        if not parent_sha:
            raise BuildsPushError("push failed: empty seed parent sha")
        _require_git(git, ["checkout", "-B", branch], cwd=tmp, token=token)
        _sync_workspace_onto_tree(Path(workspace), tmp)
        _require_git(git, ["add", "-A"], cwd=tmp, token=token)
        commit = git(
            [
                "-c",
                f"user.email={GIT_EMAIL}",
                "-c",
                f"user.name={GIT_NAME}",
                "commit",
                "--allow-empty",
                "-m",
                "factory: seed cli-pivot workspace",
            ],
            cwd=tmp,
        )
        _require_git_result(commit, "commit", token)
        sha_proc = _require_git(git, ["rev-parse", "HEAD"], cwd=tmp, token=token)
        seed_sha = (sha_proc.stdout or "").strip()
        if not seed_sha:
            raise BuildsPushError("push failed: empty seed sha")
        pushed = git(["push", "-u", "origin", f"HEAD:{branch}"], cwd=tmp)
        if pushed.returncode != 0:
            raise BuildsPushError(
                "push failed before agent start: "
                + _scrub(pushed.stderr or pushed.stdout, token)
            )
        return BuildsRef(
            owner=owner,
            repo=name,
            repository_url=repo_url,
            branch=branch,
            seed_sha=seed_sha,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _require_git(
    git: Callable[..., subprocess.CompletedProcess],
    args: Sequence[str],
    *,
    cwd: Path,
    token: str,
) -> subprocess.CompletedProcess:
    result = git(list(args), cwd=cwd)
    return _require_git_result(result, _scrub(" ".join(args), token), token)


def _require_git_result(
    result: subprocess.CompletedProcess,
    label: str,
    token: str,
) -> subprocess.CompletedProcess:
    if result.returncode != 0:
        raise BuildsPushError(
            f"push failed before agent start ({label}): "
            + _scrub(result.stderr or result.stdout, token)
        )
    return result


def github_request(
    method: str,
    path: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
    timeout_s: float = 30.0,
) -> Tuple[int, Any]:
    url = path if path.startswith("https://") else f"{GITHUB_API}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CerebrumFactory-N1a",
    }
    req = Request(url, headers=headers, method=method.upper())
    try:
        with opener(req, timeout=timeout_s) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        status = int(exc.code)
    except URLError as exc:
        raise BuildsPushError(f"GitHub API down: {exc.reason}") from exc
    text = raw.decode("utf-8", errors="replace") if raw else ""
    if not text:
        return status, {}
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def _file_content(payload: Mapping[str, Any]) -> Optional[str]:
    content = payload.get("content")
    if not content:
        return None
    encoding = str(payload.get("encoding") or "base64").lower()
    raw = str(content).replace("\n", "")
    if encoding == "base64":
        return base64.b64decode(raw).decode("utf-8")
    return str(content)


def _decode_receipt(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        text = _file_content(payload)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text
    return payload


def fetch_commit_sha(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> str:
    """Resolve a branch or SHA to a commit SHA via the GitHub commits API."""
    path = f"/repos/{owner}/{repo}/commits/{quote(ref, safe='')}"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status >= 400 or not isinstance(body, Mapping):
        raise BuildsPushError(f"GitHub API down: commit {ref} HTTP {status}")
    sha = str(body.get("sha") or "").strip()
    if not sha:
        raise BuildsPushError(f"GitHub API: empty sha for {ref}")
    return sha


def list_session_build_refs(
    owner: str,
    repo: str,
    session_id: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> List[Tuple[str, str]]:
    """``(branch, sha)`` pairs for ``build/<session>-*`` on cerebrum-builds."""
    sid = sanitize_session_id(session_id)
    prefix = f"heads/build/{sid}"
    path = f"/repos/{owner}/{repo}/git/matching-refs/{quote(prefix, safe='/')}"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status == 404:
        return []
    if status >= 400 or not isinstance(body, list):
        raise BuildsPushError(f"GitHub API down: matching-refs HTTP {status}")
    found: List[Tuple[str, str]] = []
    for item in body:
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("ref") or "").strip()
        obj = item.get("object") if isinstance(item.get("object"), Mapping) else {}
        sha = str(obj.get("sha") or "").strip()
        branch = ref.removeprefix("refs/heads/")
        if branch.startswith(f"build/{sid}") and sha:
            found.append((branch, sha))
    return found


def fetch_receipt(
    ref: BuildsRef,
    *,
    env: Mapping[str, str],
    branch: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
) -> Any:
    token = builds_token(env)
    if not token:
        raise BuildsPushError(f"{BUILDS_TOKEN_ENV} missing")
    head = branch or ref.branch
    for name in RECEIPT_NAMES:
        path = (
            f"/repos/{ref.owner}/{ref.repo}/contents/{quote(name, safe='/')}"
            f"?ref={quote(head, safe='')}"
        )
        status, body = github_request("GET", path, token=token, opener=opener)
        if status == 404:
            continue
        if status >= 400:
            raise BuildsPushError(f"GitHub API down: contents {name} HTTP {status}")
        decoded = _decode_receipt(body)
        if decoded is not None:
            return decoded
    return None


def _changed_paths(files: Sequence[Mapping[str, Any]]) -> List[str]:
    found: List[str] = []

    def _add(raw: Any) -> None:
        path = str(raw or "").replace("\\", "/").lstrip("./")
        if path and path not in found and path != "/dev/null":
            found.append(path)

    for item in files:
        _add(item.get("filename"))
        _add(item.get("previous_filename"))
    return found


def _unified_diff(files: Sequence[Mapping[str, Any]]) -> str:
    chunks: List[str] = []
    for item in files:
        name = str(item.get("filename") or "").replace("\\", "/")
        if not name:
            continue
        prev = str(item.get("previous_filename") or name).replace("\\", "/")
        patch = item.get("patch")
        chunks.append(f"diff --git a/{prev} b/{name}")
        if patch:
            chunks.append(f"--- a/{prev}")
            chunks.append(f"+++ b/{name}")
            chunks.append(str(patch).rstrip("\n"))
    return "\n".join(chunks)



def session_id_from_build_branch(branch: str) -> str:
    """``build/<session>-<8hex>`` → session id (best-effort)."""
    name = str(branch or "").strip().removeprefix("refs/heads/").removeprefix("build/")
    if not name:
        return ""
    head, sep, tail = name.rpartition("-")
    if sep and len(tail) == 8 and all(c in "0123456789abcdef" for c in tail.lower()):
        return head
    return name


def _ref_resolves(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> bool:
    """True when GitHub commits API can resolve ``ref`` (branch or SHA)."""
    if not ref:
        return False
    path = f"/repos/{owner}/{repo}/commits/{quote(ref, safe='')}"
    status, body = github_request("GET", path, token=token, opener=opener)
    return status < 400 and isinstance(body, Mapping) and bool(str(body.get("sha") or "").strip())


def _compare_seed_head(
    ref: BuildsRef,
    head: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> Tuple[int, Any]:
    compare_path = (
        f"/repos/{ref.owner}/{ref.repo}/compare/"
        f"{quote(ref.seed_sha, safe='')}...{quote(head, safe='')}"
    )
    return github_request("GET", compare_path, token=token, opener=opener)


def collect_branch(
    ref: BuildsRef,
    *,
    env: Mapping[str, str],
    branch: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = COMPARE_404_RETRIES,
    retry_sleep_s: float = COMPARE_404_SLEEP_S,
) -> Tuple[Any, List[str], str]:
    """Receipt + changed paths + unified diff vs the pre-agent seed commit.

    Compare HTTP 404 means a missing seed and/or head (BA never pushed, branch
    deleted, head renamed, or GitHub ref lag) — not a GitHub outage. Retry,
    rediscover ``build/<session>-*`` tips, fall back to the seed branch name,
    then fail closed with a clear retryable-infra diagnosis.
    """
    token = builds_token(env)
    if not token:
        raise BuildsPushError(f"{BUILDS_TOKEN_ENV} missing")
    head = branch or ref.branch
    status, body = _compare_seed_head(ref, head, token=token, opener=opener)

    if status == 404:
        attempts = max(0, int(retries))
        for _ in range(attempts):
            sleep(float(retry_sleep_s))
            status, body = _compare_seed_head(ref, head, token=token, opener=opener)
            if status != 404:
                break

    if status == 404:
        seed_ok = _ref_resolves(
            ref.owner, ref.repo, ref.seed_sha, token=token, opener=opener
        )
        head_ok = _ref_resolves(ref.owner, ref.repo, head, token=token, opener=opener)

        # Head renamed / deleted tip: try other build/<session>-* refs, then seed branch.
        candidates: List[str] = []
        if ref.branch and ref.branch != head:
            candidates.append(ref.branch)
        sid = session_id_from_build_branch(head) or session_id_from_build_branch(
            ref.branch
        )
        if sid:
            try:
                alts = list_session_build_refs(
                    ref.owner, ref.repo, sid, token=token, opener=opener
                )
            except BuildsPushError:
                alts = []
            for alt_branch, _sha in sorted(alts, key=lambda row: row[0]):
                if alt_branch not in candidates and alt_branch != head:
                    candidates.append(alt_branch)

        for alt in candidates:
            status, body = _compare_seed_head(ref, alt, token=token, opener=opener)
            if status == 404:
                continue
            if status < 400 and isinstance(body, Mapping):
                head = alt
                head_ok = True
                break

        if status == 404:
            seed_label = "present" if seed_ok else "missing"
            head_label = "present" if head_ok else "missing"
            seed_short = (ref.seed_sha or "")[:12] or "?"
            raise BuildsPushError(
                f"compare HTTP 404: seed={seed_label} head={head_label} "
                f"(seed={seed_short} head={head}) — retryable infra "
                f"(branch missing after BA, renamed, or not yet visible)"
            )

    if status >= 400 or not isinstance(body, Mapping):
        # Non-404 transport/API failures stay "API down".
        raise BuildsPushError(f"GitHub API down: compare HTTP {status}")
    files = [f for f in (body.get("files") or []) if isinstance(f, Mapping)]
    receipt = fetch_receipt(ref, env=env, branch=head, opener=opener)
    return receipt, _changed_paths(files), _unified_diff(files)
