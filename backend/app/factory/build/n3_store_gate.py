"""Ingest cerebrum-builds ``store-gate`` 12/12 into a live Factory session.

After cli-pivot ``HANDOFF_TO_N3`` (receipt+diff clean), N3 is the GitHub
Actions store-gate on the session's ``build/**`` branch — not another
WRITER / Background Agent pass. This module polls the commit status
context ``store-gate`` (artifact ``store_gate.json`` is optional score
detail) and, on 12/12 success, stamps STORE green + pilot_ready so
package ship can unlock.

Fail-closed when the status is missing, red, or score ≠ 12/12.
Never dispatches WRITER / cli_pivot / BA.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote
from urllib.request import urlopen

from app.factory.build.builds_push import (
    BUILDS_TOKEN_ENV,
    BuildsPushError,
    builds_token,
    fetch_commit_sha,
    github_request,
    list_session_build_refs,
    parse_builds_repo,
)
from app.factory.build.cli_receipt import HANDOFF_TO_N3
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    AcceptanceLine,
    AcceptanceReport,
    write_acceptance_report,
)

logger = logging.getLogger("cerebrumdev.factory.n3_store_gate")

STORE_GATE_CONTEXT = "store-gate"
N3_STORE_GATE_GREEN = "N3_STORE_GATE_GREEN"
N3_STORE_GATE_FAILED = "N3_STORE_GATE_FAILED"
N3_STORE_GATE_TIMEOUT = "N3_STORE_GATE_TIMEOUT"
N3_STORE_GATE_MISSING = "N3_STORE_GATE_MISSING"
N3_FAIL_HONESTY = frozenset(
    {N3_STORE_GATE_FAILED, N3_STORE_GATE_TIMEOUT, N3_STORE_GATE_MISSING}
)

DEFAULT_POLL_S = 15.0
DEFAULT_WALL_S = 1800.0
SCORE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

#: N3 G-floor names → Factory ``ACCEPTANCE_CHECK_NAMES`` aliases.
N3_NAME_ALIASES = {
    "ci_present_full_suite": "ci_present_and_full_suite",
    "authorship==receipt": "authorship_floor",
}

_N3_THREADS: Dict[str, threading.Thread] = {}
_N3_GUARD = threading.Lock()


@dataclass(frozen=True)
class BuildsTarget:
    owner: str
    repo: str
    sha: str
    branch: str = ""
    session_id: str = ""


@dataclass
class StoreGateSnapshot:
    """One read of the ``store-gate`` commit status (and optional artifact)."""

    state: str = ""
    context: str = STORE_GATE_CONTEXT
    description: str = ""
    passed: Optional[int] = None
    total: Optional[int] = None
    ok: bool = False
    missing: bool = True
    timeout: bool = False
    pending: bool = False
    sha: str = ""
    branch: str = ""
    owner: str = ""
    repo: str = ""
    via: str = "github-commit-status:store-gate"
    detail: str = ""
    lines: List[AcceptanceLine] = field(default_factory=list)

    @property
    def score(self) -> str:
        if self.passed is None or self.total is None:
            return ""
        return f"{self.passed}/{self.total}"

    @property
    def is_12_of_12(self) -> bool:
        return (
            not self.missing
            and not self.timeout
            and self.ok
            and self.state == "success"
            and self.passed == ACCEPTANCE_REQUIRED
            and self.total == ACCEPTANCE_REQUIRED
        )


@dataclass
class IngestResult:
    honesty: str
    ok: bool
    pending: bool = False
    detail: str = ""
    snapshot: Optional[StoreGateSnapshot] = None
    already: bool = False

    def to_dict(self) -> Dict[str, Any]:
        snap = self.snapshot
        return {
            "honesty": self.honesty,
            "ok": self.ok,
            "pending": self.pending,
            "detail": self.detail,
            "already": self.already,
            "green": self.ok,
            "score": snap.score if snap else "",
            "sha": snap.sha if snap else "",
            "branch": snap.branch if snap else "",
        }


def n3_poll_s(env: Optional[Mapping[str, str]] = None) -> float:
    blob = env if env is not None else os.environ
    raw = str(blob.get("FACTORY_N3_POLL_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return DEFAULT_POLL_S
    return DEFAULT_POLL_S


def n3_wall_s(env: Optional[Mapping[str, str]] = None) -> float:
    blob = env if env is not None else os.environ
    raw = str(blob.get("FACTORY_N3_WALL_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return DEFAULT_WALL_S
    return DEFAULT_WALL_S


def _ledger(output_dir: Path | str) -> BuildLedger:
    return BuildLedger(Path(output_dir) / "build_ledger.jsonl")


def _event_honesty(event: Any) -> str:
    payload = getattr(event, "payload", None) or {}
    return str(payload.get("honesty") or "").strip()


def _event_outcome(event: Any) -> str:
    payload = getattr(event, "payload", None) or {}
    return str(payload.get("outcome") or "").strip()


def handoff_awaiting_n3(output_dir: Path | str) -> bool:
    """True when cli-pivot handed off and N3 has not yet been ingested."""
    path = Path(output_dir) / "build_ledger.jsonl"
    if not path.is_file():
        return False
    ledger = BuildLedger(path)
    try:
        events = ledger.events()
    except Exception:  # noqa: BLE001
        return False
    saw_handoff = False
    for event in events:
        honesty = _event_honesty(event)
        outcome = _event_outcome(event)
        if honesty == N3_STORE_GATE_GREEN or (
            event.kind is EventKind.RUN_SUCCEEDED
            and honesty == N3_STORE_GATE_GREEN
        ):
            return False
        if honesty in N3_FAIL_HONESTY:
            return False
        if honesty == HANDOFF_TO_N3 or outcome == HANDOFF_TO_N3:
            saw_handoff = True
            continue
        if event.kind is EventKind.RUN_SUCCEEDED:
            return False
        if event.kind is EventKind.RUN_FAILED and saw_handoff:
            if honesty == HANDOFF_TO_N3 or outcome == HANDOFF_TO_N3:
                continue
            return False
    return saw_handoff


def n3_ingest_live(output_dir: Path | str) -> bool:
    key = str(Path(output_dir).resolve())
    thread = _N3_THREADS.get(key)
    return thread is not None and thread.is_alive()


def builds_fields_from_ledger(output_dir: Path | str) -> Dict[str, Any]:
    """Latest builds SHA/branch/authored ids recorded on HANDOFF notes."""
    path = Path(output_dir) / "build_ledger.jsonl"
    found: Dict[str, Any] = {}
    if not path.is_file():
        return found
    try:
        events = BuildLedger(path).events()
    except Exception:  # noqa: BLE001
        return found
    keys = (
        "builds_sha",
        "builds_branch",
        "builds_owner",
        "builds_repo",
        "cli_authored_ids",
    )
    for event in events:
        payload = getattr(event, "payload", None) or {}
        blobs: List[Mapping[str, Any]] = [payload]
        nested = payload.get("cli_pivot")
        if isinstance(nested, Mapping):
            blobs.append(nested)
        for blob in blobs:
            for key in keys:
                value = blob.get(key)
                if value:
                    found[key] = value
    return found


def resolve_builds_target(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    session_id: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
) -> BuildsTarget:
    blob = env if env is not None else os.environ
    token = builds_token(blob)
    if not token:
        raise BuildsPushError(
            f"{BUILDS_TOKEN_ENV} missing — fail-closed; cannot poll store-gate"
        )
    owner, repo, _url = parse_builds_repo(blob)
    recorded = builds_fields_from_ledger(output_dir)
    sha = str(recorded.get("builds_sha") or "").strip()
    branch = str(recorded.get("builds_branch") or "").strip()
    owner = str(recorded.get("builds_owner") or owner).strip()
    repo = str(recorded.get("builds_repo") or repo).strip()
    sid = str(session_id or "").strip()
    if not sid:
        from app.factory.build.orphan_recovery import session_id_from_output

        sid = session_id_from_output(output_dir) or ""
    if sha:
        return BuildsTarget(
            owner=owner, repo=repo, sha=sha, branch=branch, session_id=sid
        )
    if branch:
        resolved = fetch_commit_sha(
            owner, repo, branch, token=token, opener=opener
        )
        return BuildsTarget(
            owner=owner, repo=repo, sha=resolved, branch=branch, session_id=sid
        )
    if not sid:
        raise BuildsPushError(
            "N3 store-gate: no builds SHA/branch on the ledger and no session id"
        )
    refs = list_session_build_refs(
        owner, repo, sid, token=token, opener=opener
    )
    if not refs:
        raise BuildsPushError(
            f"N3 store-gate: no build/{sid}-* branch on {owner}/{repo}"
        )
    branch, sha = refs[-1]
    return BuildsTarget(
        owner=owner, repo=repo, sha=sha, branch=branch, session_id=sid
    )


def _parse_score(text: str) -> Tuple[Optional[int], Optional[int]]:
    match = SCORE_RE.search(text or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _factory_check_name(name: str) -> str:
    raw = str(name or "").strip()
    return N3_NAME_ALIASES.get(raw, raw)


def report_from_store_gate_payload(raw: Mapping[str, Any]) -> AcceptanceReport:
    """Map a GHA ``store_gate.json`` (N3 names) onto Factory aliases."""
    lines: List[AcceptanceLine] = []
    seen: set[str] = set()
    for item in raw.get("lines") or []:
        if not isinstance(item, Mapping):
            continue
        name = _factory_check_name(str(item.get("name") or ""))
        if name not in ACCEPTANCE_CHECK_NAMES or name in seen:
            continue
        seen.add(name)
        lines.append(
            AcceptanceLine(
                name=name,
                status=str(item.get("status") or "FAIL").upper(),
                detail=str(item.get("detail") or ""),
            )
        )
    by_name = {line.name: line for line in lines}
    ordered = [
        by_name.get(
            name,
            AcceptanceLine(name=name, status="FAIL", detail="omitted"),
        )
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    passed = int(raw.get("passed") or sum(1 for line in ordered if line.satisfied))
    total = int(raw.get("total") or ACCEPTANCE_REQUIRED)
    if total < ACCEPTANCE_REQUIRED:
        total = ACCEPTANCE_REQUIRED
    ok = bool(raw.get("ok")) and passed >= total and all(
        line.satisfied for line in ordered
    )
    return AcceptanceReport(
        passed=passed,
        total=total,
        ok=ok,
        lines=ordered,
        missing=False,
        via=str(raw.get("via") or "store_gate.json"),
        detail=str(raw.get("detail") or raw.get("score") or f"{passed}/{total}"),
    )


def report_from_snapshot(snap: StoreGateSnapshot) -> AcceptanceReport:
    if snap.lines:
        return AcceptanceReport(
            passed=int(snap.passed or 0),
            total=int(snap.total or ACCEPTANCE_REQUIRED),
            ok=snap.is_12_of_12,
            lines=list(snap.lines),
            missing=False,
            via=snap.via,
            detail=snap.detail or snap.score,
        )
    status = "PASS" if snap.is_12_of_12 else "FAIL"
    detail = snap.description or snap.detail or snap.score or "store-gate"
    lines = [
        AcceptanceLine(name=name, status=status, detail=detail)
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    passed = snap.passed if snap.passed is not None else 0
    total = snap.total if snap.total is not None else ACCEPTANCE_REQUIRED
    return AcceptanceReport(
        passed=passed,
        total=total,
        ok=snap.is_12_of_12,
        lines=lines,
        missing=snap.missing,
        via=snap.via,
        detail=snap.detail or snap.score or "store-gate",
    )


def fetch_store_gate_status(
    target: BuildsTarget,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
) -> StoreGateSnapshot:
    """Read the latest ``store-gate`` commit status. Fail-closed if absent."""
    blob = env if env is not None else os.environ
    token = builds_token(blob)
    if not token:
        return StoreGateSnapshot(
            missing=True,
            detail=f"{BUILDS_TOKEN_ENV} missing",
            sha=target.sha,
            branch=target.branch,
            owner=target.owner,
            repo=target.repo,
        )
    path = f"/repos/{target.owner}/{target.repo}/commits/{quote(target.sha, safe='')}/statuses"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status >= 400:
        return StoreGateSnapshot(
            missing=True,
            detail=f"GitHub statuses HTTP {status}",
            sha=target.sha,
            branch=target.branch,
            owner=target.owner,
            repo=target.repo,
        )
    rows = body if isinstance(body, list) else []
    match: Optional[Mapping[str, Any]] = None
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("context") or "").strip() == STORE_GATE_CONTEXT:
            match = item
            break
    if match is None:
        return StoreGateSnapshot(
            missing=True,
            pending=True,
            detail="store-gate commit status is missing",
            sha=target.sha,
            branch=target.branch,
            owner=target.owner,
            repo=target.repo,
        )
    state = str(match.get("state") or "").strip().lower()
    description = str(match.get("description") or "")
    passed, total = _parse_score(description)
    ok = (
        state == "success"
        and passed == ACCEPTANCE_REQUIRED
        and total == ACCEPTANCE_REQUIRED
    )
    pending = state in {"pending", "expected"} or (
        not ok and state not in {"failure", "error", "success"}
    )
    if state == "success" and not ok:
        pending = False
    return StoreGateSnapshot(
        state=state,
        description=description,
        passed=passed,
        total=total,
        ok=ok,
        missing=False,
        pending=pending and not ok,
        sha=target.sha,
        branch=target.branch,
        owner=target.owner,
        repo=target.repo,
        detail=description or f"store-gate {state}",
    )


def wait_for_store_gate(
    target: BuildsTarget,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: Optional[float] = None,
    wall_s: Optional[float] = None,
) -> StoreGateSnapshot:
    """Bounded poll. Returns on 12/12, red, or timeout. Never launches a BA."""
    blob = env if env is not None else os.environ
    interval = n3_poll_s(blob) if poll_s is None else max(0.0, float(poll_s))
    wall = n3_wall_s(blob) if wall_s is None else max(0.0, float(wall_s))
    deadline = clock() + wall
    last = StoreGateSnapshot(
        missing=True,
        pending=True,
        sha=target.sha,
        branch=target.branch,
        owner=target.owner,
        repo=target.repo,
        detail="store-gate poll not started",
    )
    while True:
        last = fetch_store_gate_status(target, env=blob, opener=opener)
        if last.is_12_of_12:
            return last
        if not last.missing and last.state in {"failure", "error"}:
            return last
        if last.state == "success" and not last.is_12_of_12:
            return last
        remaining = deadline - clock()
        if remaining <= 0:
            last.timeout = True
            last.pending = False
            last.detail = (
                last.detail + "; N3 store-gate poll timed out"
                if last.detail
                else "N3 store-gate poll timed out"
            )
            return last
        sleep(min(interval, remaining) if interval > 0 else 0.0)


def _cli_authored_ids(output_dir: Path | str) -> List[str]:
    recorded = builds_fields_from_ledger(output_dir)
    ids = recorded.get("cli_authored_ids") or []
    if isinstance(ids, str):
        return [ids] if ids.strip() else []
    return [str(item).strip() for item in ids if str(item).strip()]


def _persist_cli_authorship(output_dir: Path, ids: Sequence[str]) -> None:
    """Keep package / Store-green floor honest from the HANDOFF receipt."""
    if not ids:
        return
    dest = Path(output_dir) / "docs" / "build_provenance.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {}
    if dest.is_file():
        try:
            raw = json.loads(dest.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except (OSError, ValueError):
            payload = {}
    dispatch = payload.get("brief_dispatch")
    if not isinstance(dispatch, dict):
        dispatch = {}
    dispatch["cli_authored_ids"] = list(ids)
    if dispatch.get("n_required") in (None, "", 0) and ids:
        dispatch["n_required"] = len(ids)
    payload["brief_dispatch"] = dispatch
    dest.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _three_gate_pilot_detail() -> str:
    from app.factory.build.product_gate import GATE_SCOPES

    return "; ".join(
        (
            "CODE PASS — %s" % GATE_SCOPES["CODE"],
            "PRODUCT PASS — %s" % GATE_SCOPES["PRODUCT"],
            "STORE PASS — %s" % GATE_SCOPES["STORE"],
        )
    )


def apply_store_gate_success(
    output_dir: Path | str,
    snap: StoreGateSnapshot,
    *,
    cli_authored_ids: Optional[Sequence[str]] = None,
) -> None:
    """Stamp acceptance k/k + STORE-green SUCCESS. Does not claim SCAFFOLD."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report = report_from_snapshot(snap)
    if not snap.is_12_of_12 or not report.ok:
        raise BuildsPushError(
            "refuse N3 SUCCESS: store-gate is not 12/12 ok=true "
            f"(score={snap.score!r} state={snap.state!r})"
        )
    write_acceptance_report(root, report)
    ids = list(cli_authored_ids or _cli_authored_ids(root))
    _persist_cli_authorship(root, ids)
    try:
        from app.factory.build.package import IDENTITY_REL, identity_document

        doc = identity_document(
            root,
            extra={
                "engine": "n3_store_gate",
                "via": snap.via,
                "builds_sha": snap.sha,
            },
        )
        ident = root / IDENTITY_REL
        ident.parent.mkdir(parents=True, exist_ok=True)
        ident.write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 — identity is not the green bit
        logger.exception("n3 ingest: package identity stamp failed at %s", root)
    ledger = _ledger(root)
    if not ledger.exists():
        ledger.start_run(product_id=root.name, inputs_hash="n3_store_gate")
    from app.factory.build.authority import BuildRole

    ledger.append(
        EventKind.GATE_PASSED,
        role=BuildRole.STORE_MANAGER,
        detail=f"STORE (n3 store-gate): {snap.score} on {snap.sha[:12]}",
        payload={
            "gate": "store_acceptance",
            "via": snap.via,
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "honesty": N3_STORE_GATE_GREEN,
        },
    )
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        role=BuildRole.STORE_MANAGER,
        detail=_three_gate_pilot_detail(),
        payload={
            "outcome": "SUCCESS",
            "cycle": "pilot",
            "pilot_ready": True,
            "honesty": N3_STORE_GATE_GREEN,
            "green": True,
            "via": snap.via,
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "cli_authored_ids": ids,
        },
    )


def apply_store_gate_failure(
    output_dir: Path | str,
    snap: StoreGateSnapshot,
    *,
    honesty: str,
) -> None:
    root = Path(output_dir)
    ledger = _ledger(root)
    if not ledger.exists():
        ledger.start_run(product_id=root.name, inputs_hash="n3_store_gate")
    from app.factory.build.authority import BuildRole

    detail = snap.detail or f"store-gate {snap.state or 'missing'} {snap.score}"
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail=detail,
        payload={
            "outcome": "FAILED_GATE",
            "cycle": "code",
            "pilot_ready": False,
            "honesty": honesty,
            "green": False,
            "next": "n3_gate",
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "state": snap.state,
        },
    )


def _failure_honesty(snap: StoreGateSnapshot) -> str:
    if snap.timeout:
        return N3_STORE_GATE_TIMEOUT
    if snap.missing and not snap.state:
        return N3_STORE_GATE_MISSING
    return N3_STORE_GATE_FAILED


def ingest_n3_store_gate(
    output_dir: Path | str,
    *,
    wait: bool = False,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: Optional[float] = None,
    wall_s: Optional[float] = None,
    session_id: Optional[str] = None,
) -> IngestResult:
    """Fetch or poll store-gate and apply the ledger stamp. Never starts a BA."""
    root = Path(output_dir)
    if not handoff_awaiting_n3(root):
        ledger_path = root / "build_ledger.jsonl"
        if ledger_path.is_file():
            try:
                ledger = BuildLedger(ledger_path)
                if ledger.succeeded() and ledger.pilot_ready():
                    return IngestResult(
                        honesty=N3_STORE_GATE_GREEN,
                        ok=True,
                        already=True,
                        detail="N3 store-gate already ingested (pilot_ready)",
                    )
            except Exception:  # noqa: BLE001
                pass
            if not handoff_awaiting_n3(root):
                # A later N3 fail is terminal; a workspace that never
                # handed off is not this seam.
                events_honesty = ""
                try:
                    for event in BuildLedger(ledger_path).events():
                        events_honesty = _event_honesty(event) or events_honesty
                except Exception:  # noqa: BLE001
                    events_honesty = ""
                if events_honesty in N3_FAIL_HONESTY:
                    return IngestResult(
                        honesty=events_honesty,
                        ok=False,
                        detail="N3 store-gate already failed-closed",
                    )
        return IngestResult(
            honesty=HANDOFF_TO_N3,
            ok=False,
            pending=False,
            detail="no HANDOFF_TO_N3 awaiting N3 on this workspace",
        )

    blob = env if env is not None else os.environ
    try:
        target = resolve_builds_target(
            root, env=blob, session_id=session_id, opener=opener
        )
    except BuildsPushError as exc:
        snap = StoreGateSnapshot(missing=True, detail=str(exc))
        apply_store_gate_failure(root, snap, honesty=N3_STORE_GATE_MISSING)
        return IngestResult(
            honesty=N3_STORE_GATE_MISSING,
            ok=False,
            detail=str(exc),
            snapshot=snap,
        )

    if wait:
        snap = wait_for_store_gate(
            target,
            env=blob,
            opener=opener,
            clock=clock,
            sleep=sleep,
            poll_s=poll_s,
            wall_s=wall_s,
        )
    else:
        snap = fetch_store_gate_status(target, env=blob, opener=opener)

    if snap.is_12_of_12:
        apply_store_gate_success(root, snap)
        return IngestResult(
            honesty=N3_STORE_GATE_GREEN,
            ok=True,
            detail=f"ingested store-gate {snap.score} on {snap.sha[:12]}",
            snapshot=snap,
        )
    if snap.pending and not wait:
        return IngestResult(
            honesty=HANDOFF_TO_N3,
            ok=False,
            pending=True,
            detail=snap.detail or "store-gate still pending",
            snapshot=snap,
        )
    honesty = _failure_honesty(snap)
    apply_store_gate_failure(root, snap, honesty=honesty)
    return IngestResult(
        honesty=honesty,
        ok=False,
        detail=snap.detail or f"store-gate fail-closed ({snap.state} {snap.score})",
        snapshot=snap,
    )


def wait_and_ingest_n3(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    **kwargs: Any,
) -> IngestResult:
    """Long-poll ingest for the background build thread after HANDOFF_TO_N3."""
    key = str(Path(output_dir).resolve())
    with _N3_GUARD:
        existing = _N3_THREADS.get(key)
        if existing is not None and existing.is_alive() and existing is not threading.current_thread():
            return IngestResult(
                honesty=HANDOFF_TO_N3,
                ok=False,
                pending=True,
                already=True,
                detail="store-gate waiter already live",
            )
        _N3_THREADS[key] = threading.current_thread()
    try:
        return ingest_n3_store_gate(
            output_dir, wait=True, env=env, opener=opener, **kwargs
        )
    finally:
        with _N3_GUARD:
            if _N3_THREADS.get(key) is threading.current_thread():
                _N3_THREADS.pop(key, None)


def start_n3_ingest_job(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Start a daemon waiter if one is not already live. Returns True if started."""
    key = str(Path(output_dir).resolve())
    with _N3_GUARD:
        existing = _N3_THREADS.get(key)
        if existing is not None and existing.is_alive():
            return False

        def _run() -> None:
            try:
                wait_and_ingest_n3(
                    output_dir, env=env, session_id=session_id
                )
            except Exception:  # noqa: BLE001
                logger.exception("n3 store-gate waiter crashed for %s", output_dir)

        thread = threading.Thread(
            target=_run,
            name=f"n3-gate-{Path(output_dir).name}",
            daemon=True,
        )
        _N3_THREADS[key] = thread
        thread.start()
        return True
