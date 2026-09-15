"""The CodeWhale writer worker (Phase 5.1/5.4).

Runs the CodeWhale (DeepSeek) coding agent headless via ``codewhale exec``
from a job queue, under the tenant-scoped store handle (Phase 1 isolation
applies to the builder too), behind an explicit concurrency cap.

VERIFIED (Phase 5.1): ``codewhale exec --auto --json`` completes a trivial
file job non-interactively — agent mode, machine-readable summary,
termination_reason "resolved", no approval-prompt hang. The local probe
result is recorded in the PR report; T5.1 re-runs it wherever the CLI
exists and SKIPS with a reason elsewhere (CI has no codewhale binary).

The receipt records what the CLI actually reports: status,
termination_reason, provider, model, tool outcomes, output. Token-level
COGS is a named gap — the CLI summary does not emit usage, so no cost
number is invented.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

WORKER_CONCURRENCY_CAPPED = "worker_concurrency_capped"
WORKER_DISPATCH_AMBIGUOUS = "worker_dispatch_ambiguous"
WORKER_CLI_MISSING = "worker_cli_missing"
WORKER_EXEC_FAILED = "worker_exec_failed"
WORKER_TIMED_OUT = "worker_timed_out"

DEFAULT_WORKER_CAP = 1
DEFAULT_WORKER_TIMEOUT_S = 1800.0

#: Phase 1's named refusal, mirrored here so the builder's isolation
#: boundary uses the same reason string as the runtime seam.
NO_AUTHENTICATED_TENANT = "no_authenticated_tenant"


class WorkerError(RuntimeError):
    """A named refusal/failure from the CodeWhale worker layer."""


def worker_cli_path() -> Optional[str]:
    return shutil.which("codewhale")


def worker_provider() -> str:
    """Model provider for the headless CLI. DeepSeek by default (the CLI's
    production provider); deployments may override via env."""
    return os.getenv("CODEWHALE_PROVIDER", "deepseek").strip() or "deepseek"


def worker_api_key() -> str:
    """The CLI's API key from the deployment env. Empty when the CLI's own
    stored config is the credential source (local dev)."""
    return (
        os.getenv("CODEWHALE_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
    )


def worker_cap() -> int:
    return int(os.getenv("FACTORY_CODEWHALE_WORKER_CAP", str(DEFAULT_WORKER_CAP)) or 1)


def worker_timeout_s() -> float:
    return float(
        os.getenv("FACTORY_CODEWHALE_WORKER_TIMEOUT_S", str(DEFAULT_WORKER_TIMEOUT_S))
    )


#: In-process job slots for one worker process. The cap is a hard refusal:
#: job N+1 is refused with a named reason, never silently run.
_active_jobs = 0
_active_jobs_lock = threading.Lock()


@dataclass(frozen=True)
class WorkerReceipt:
    """What the CLI actually reported. No invented fields."""

    status: str
    termination_reason: Optional[str]
    provider: str = ""
    model: str = ""
    output: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    error_category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "termination_reason": self.termination_reason,
            "provider": self.provider,
            "model": self.model,
            "output": self.output,
            "tools": list(self.tools),
            "error": self.error,
            "error_category": self.error_category,
        }


@contextmanager
def worker_job_slot(tenant_store: Any) -> Iterator[None]:
    """Acquire one concurrency slot under the tenant store handle.

    Phase 1 applies to the builder: the store must already be BOUND for
    this job (resolved from the authenticated principal). An unbound job
    refuses with ``no_authenticated_tenant`` — P5's mutation runs the
    worker unbound and the suite goes RED.
    """
    _require_bound_tenant(tenant_store)
    global _active_jobs
    with _active_jobs_lock:
        if _active_jobs >= worker_cap():
            raise WorkerError(
                f"{WORKER_CONCURRENCY_CAPPED}: {worker_cap()} job slot(s), "
                f"{_active_jobs} active — job refused, never silently run"
            )
        _active_jobs += 1
    try:
        yield
    finally:
        with _active_jobs_lock:
            _active_jobs -= 1


def _require_bound_tenant(tenant_store: Any) -> None:
    """The worker never runs without a bound tenant store handle.

    The handle IS the Phase 1 boundary: it was resolved from the
    authenticated principal upstream; the worker accepts only that handle,
    never a client-supplied name. None = unauthenticated = refusal.
    """
    if tenant_store is None:
        raise WorkerError(
            f"{NO_AUTHENTICATED_TENANT}: the worker job has no bound tenant "
            "store — refusing to run the builder without isolation"
        )


def run_worker_job(
    prompt: str,
    checkout_dir: Path | str,
    *,
    tenant_store: Any = None,
    timeout_s: Optional[float] = None,
) -> WorkerReceipt:
    """Run one headless CodeWhale exec — non-interactive, JSON summary.

    T5.1: no approval prompt may hang the worker; the CLI's --auto mode is
    the documented non-interactive automation path. The job runs INSIDE a
    worker slot; beyond the cap it refuses, never queues-and-forgets.
    """
    cli = worker_cli_path()
    # The tenant boundary precedes everything: an unbound job is refused
    # before the binary is even looked up — isolation is the first gate,
    # and P5 must hold on runners with no codewhale installed.
    _require_bound_tenant(tenant_store)
    if cli is None:
        raise WorkerError(
            f"{WORKER_CLI_MISSING}: codewhale executable not found — the "
            "worker cannot run headless"
        )
    with worker_job_slot(tenant_store):
        cwd = Path(checkout_dir)
        cwd.mkdir(parents=True, exist_ok=True)
        timeout = timeout_s if timeout_s is not None else worker_timeout_s()
        # codewhale 0.9.13 parses --provider/--api-key as GLOBAL flags:
        # they must precede `exec`. After the subcommand they are refused
        # ("--provider must be placed before `exec`") and the job dies in
        # under a second with zero authored artifacts.
        argv = [cli]
        # Headless deployments pass credentials explicitly; local dev lets
        # the CLI read its own stored config when the env key is absent.
        api_key = worker_api_key()
        if api_key:
            argv += ["--provider", worker_provider(), "--api-key", api_key]
        argv += ["exec", "--auto", "--json"]
        argv.append(prompt)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerError(
                f"{WORKER_TIMED_OUT}: headless job exceeded {timeout}s"
            ) from exc
        if proc.returncode != 0:
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: exit {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '')[:400]}"
            )
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: non-JSON summary from the CLI"
            ) from exc
        return WorkerReceipt(
            status=str(payload.get("status") or ""),
            termination_reason=payload.get("termination_reason"),
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            output=str(payload.get("output") or ""),
            tools=[
                dict(t) for t in (payload.get("tools") or []) if isinstance(t, dict)
            ],
            error=payload.get("error"),
            error_category=payload.get("error_category"),
        )
