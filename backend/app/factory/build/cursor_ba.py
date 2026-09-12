"""Live Cursor Background Agent launch for the Factory CLI-pivot (N1a).

Creates one agent against the scratch ``build/**`` branch already pushed
to cerebrum-builds, polls until FINISHED / FAILED / wall, then collects
the branch receipt + diff. Model is the Cursor account default — never
hardcoded here.

Do not import kimi / FACTORY_CODE_CLI / author fallbacks.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.factory.build.builds_push import (
    BuildsRef,
    collect_branch,
    push_workspace,
)

CURSOR_API_BASE = "https://api.cursor.com"
CURSOR_KEY_ENVS = (
    "CURSOR_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "FACTORY_CURSOR_API_KEY",
)

# Fixed launch prompt — do not paraphrase.
LAUNCH_PROMPT = (
    "Execute docs/coder_brief.md exactly. Commit all work to this branch. "
    "End by committing receipt.json."
)

DEFAULT_START_TIMEOUT_S = 90.0
DEFAULT_POLL_S = 5.0
HTTP_TIMEOUT_S = 30.0

FINISHED = "FINISHED"
FAILED = "FAILED"
CREATING = "CREATING"
TERMINAL_FAIL = frozenset(
    {"FAILED", "EXPIRED", "CANCELLED", "CANCELED", "STOPPED", "ERROR"}
)


class CursorBAError(RuntimeError):
    """Cursor API / agent infra miss (never started, FAILED, API down)."""


@dataclass
class BackgroundAgentResult:
    """Live BA outcome. ``cli_pivot`` maps this onto ``ExecutorLaunch``."""

    started: bool
    hung: bool = False
    elapsed_s: float = 0.0
    spent_usd: float = 0.0
    receipt: Any = None
    changed_paths: List[str] = field(default_factory=list)
    unified_diff: str = ""
    agent_id: str = ""
    branch: str = ""


def cursor_api_key(env: Mapping[str, str]) -> str:
    for name in CURSOR_KEY_ENVS:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def extract_spent_usd(payload: Mapping[str, Any]) -> float:
    blobs: List[Mapping[str, Any]] = [payload]
    for key in ("usage", "billing", "cost"):
        item = payload.get(key)
        if isinstance(item, Mapping):
            blobs.append(item)
    for blob in blobs:
        for name in (
            "spent_usd",
            "spentUsd",
            "cost_usd",
            "costUsd",
            "total_cost_usd",
            "totalCostUsd",
        ):
            if name not in blob:
                continue
            try:
                return float(blob[name])
            except (TypeError, ValueError):
                continue
    return 0.0


def agent_branch_name(payload: Mapping[str, Any], fallback: str) -> str:
    target = payload.get("target")
    if isinstance(target, Mapping):
        name = str(target.get("branchName") or target.get("branch_name") or "").strip()
        if name:
            return name
    return fallback


def _json_request(
    method: str,
    url: str,
    *,
    api_key: str,
    body: Optional[Mapping[str, Any]] = None,
    opener: Callable[..., Any] = urlopen,
    timeout_s: float = HTTP_TIMEOUT_S,
) -> Tuple[int, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with opener(req, timeout=timeout_s) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        status = int(exc.code)
    except URLError as exc:
        raise CursorBAError(f"Cursor API down: {exc.reason}") from exc
    text = raw.decode("utf-8", errors="replace") if raw else ""
    parsed: Any = {}
    if text:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = {"raw": text}
    return status, parsed


def create_agent(
    *,
    api_key: str,
    repository_url: str,
    ref: str,
    opener: Callable[..., Any] = urlopen,
) -> Dict[str, Any]:
    """POST /v0/agents — work on the scratch branch; do not open a PR."""
    payload = {
        "prompt": {"text": LAUNCH_PROMPT},
        "source": {"repository": repository_url, "ref": ref},
        "target": {"autoCreatePr": False, "autoBranch": False},
    }
    status, body = _json_request(
        "POST",
        f"{CURSOR_API_BASE}/v0/agents",
        api_key=api_key,
        body=payload,
        opener=opener,
    )
    if status >= 400 or not isinstance(body, Mapping) or not body.get("id"):
        raise CursorBAError(
            f"Cursor API down or agent never started (create HTTP {status})"
        )
    return dict(body)


def get_agent(
    agent_id: str,
    *,
    api_key: str,
    opener: Callable[..., Any] = urlopen,
) -> Dict[str, Any]:
    status, body = _json_request(
        "GET",
        f"{CURSOR_API_BASE}/v0/agents/{agent_id}",
        api_key=api_key,
        opener=opener,
    )
    if status >= 400 or not isinstance(body, Mapping):
        raise CursorBAError(f"Cursor API down (get HTTP {status})")
    return dict(body)


def poll_agent(
    agent_id: str,
    *,
    api_key: str,
    wall_s: float,
    start_timeout_s: float = DEFAULT_START_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Poll until FINISHED / FAILED / wall. FAILED and never-started raise."""
    t0 = clock()
    last: Dict[str, Any] = {"id": agent_id, "status": CREATING}
    left_creating = False
    while True:
        last = get_agent(agent_id, api_key=api_key, opener=opener)
        status = str(last.get("status") or "").upper()
        elapsed = float(clock() - t0)
        last["_elapsed_s"] = elapsed
        if status and status != CREATING:
            left_creating = True
        if status == FINISHED:
            last["_hung"] = elapsed > float(wall_s)
            last["_started"] = True
            return last
        if status in TERMINAL_FAIL:
            raise CursorBAError(f"agent {status}")
        if elapsed > float(wall_s):
            if not left_creating:
                raise CursorBAError("agent never started")
            last["_hung"] = True
            last["_started"] = True
            return last
        if not left_creating and elapsed > float(start_timeout_s):
            raise CursorBAError("agent never left CREATING")
        remaining = max(0.0, float(wall_s) - elapsed)
        sleep(min(float(poll_s), remaining if remaining > 0 else float(poll_s)))


def run_background_agent(
    *,
    workspace: Path,
    wall_s: float,
    env: Mapping[str, str],
    session_id: str,
    opener: Callable[..., Any] = urlopen,
    run_git: Optional[Callable[..., Any]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    start_timeout_s: float = DEFAULT_START_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    push: Optional[Callable[..., BuildsRef]] = None,
    collect: Optional[Callable[..., Tuple[Any, List[str], str]]] = None,
) -> BackgroundAgentResult:
    """Push workspace → create BA → poll → collect receipt/diff."""
    api_key = cursor_api_key(env)
    if not api_key:
        raise CursorBAError("Cursor executor keys absent")
    pusher = push or push_workspace
    collector = collect or collect_branch
    ref = pusher(
        Path(workspace),
        env=env,
        session_id=session_id,
        **({"run_git": run_git} if push is None and run_git is not None else {}),
    )
    created = create_agent(
        api_key=api_key,
        repository_url=ref.repository_url,
        ref=ref.branch,
        opener=opener,
    )
    agent_id = str(created.get("id") or "").strip()
    if not agent_id:
        raise CursorBAError("agent never started")
    polled = poll_agent(
        agent_id,
        api_key=api_key,
        wall_s=wall_s,
        start_timeout_s=start_timeout_s,
        poll_s=poll_s,
        opener=opener,
        clock=clock,
        sleep=sleep,
    )
    elapsed = float(polled.get("_elapsed_s") or 0.0)
    hung = bool(polled.get("_hung"))
    started = bool(polled.get("_started", True))
    work_branch = agent_branch_name(polled, ref.branch)
    if hung or not started:
        return BackgroundAgentResult(
            started=started,
            hung=True if hung else False,
            elapsed_s=elapsed,
            spent_usd=extract_spent_usd(polled),
            agent_id=agent_id,
            branch=work_branch,
        )
    receipt, paths, diff = collector(
        ref, env=env, branch=work_branch, opener=opener
    )
    return BackgroundAgentResult(
        started=True,
        hung=False,
        elapsed_s=elapsed,
        spent_usd=extract_spent_usd(polled),
        receipt=receipt,
        changed_paths=list(paths),
        unified_diff=diff,
        agent_id=agent_id,
        branch=work_branch,
    )
