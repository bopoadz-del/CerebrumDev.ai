"""The CodeWhale writer worker (Phase 5.1/5.4).

Runs the CodeWhale (DeepSeek) coding agent headless via ``codewhale exec``
from a job queue, under the tenant-scoped store handle (Phase 1 isolation
applies to the builder too), behind an explicit concurrency cap.

CONCURRENCY IS TWO-DIMENSIONAL. CerebrumDev.ai is multi-tenant: every
account gets its own shell and the coder deploys its own agents inside
it. A single process-wide counter cannot express that, so slots are
accounted on two independent axes:

    process cap  -- HOST PROTECTION. Total jobs in flight on this
                    instance, across every tenant. Sized from the box's
                    memory budget (see WORKER_PROFILES).
    tenant cap   -- FAIRNESS. Jobs in flight for ONE bound tenant. Stops
                    one account consuming every slot on the box.

Both refusals are NAMED and HARD: the job is refused, never silently run
and never silently queued, and the two exhaustion modes carry different
reason tokens so an operator reading a build log can tell "this user is
at their limit" from "the box is full".

VERIFIED (Phase 5.1): ``codewhale exec --auto --json`` completes a trivial
file job non-interactively -- agent mode, machine-readable summary,
termination_reason "resolved", no approval-prompt hang. The local probe
result is recorded in the PR report; T5.1 re-runs it wherever the CLI
exists and SKIPS with a reason elsewhere (CI has no codewhale binary).

The receipt records what the CLI actually reports: status,
termination_reason, provider, model, tool outcomes, output. Token-level
COGS is a named gap -- the CLI summary does not emit usage, so no cost
number is invented.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("cerebrumdev.factory.codewhale_worker")

WORKER_CONCURRENCY_CAPPED = "worker_concurrency_capped"
WORKER_DISPATCH_AMBIGUOUS = "worker_dispatch_ambiguous"
WORKER_CLI_MISSING = "worker_cli_missing"
WORKER_EXEC_FAILED = "worker_exec_failed"
WORKER_TIMED_OUT = "worker_timed_out"

#: The two exhaustion modes behind WORKER_CONCURRENCY_CAPPED. The capped
#: token stays the LEADING token of both messages -- the documented
#: contract is preserved, the scope is ADDED. Grep one or the other to
#: separate "the box is full" (page someone) from "this user hit their
#: own limit" (the box may be idle; nobody else is affected).
PROCESS_SLOTS_EXHAUSTED = "process_slots_exhausted"
TENANT_SLOTS_EXHAUSTED = "tenant_slots_exhausted"

#: A handle that carries no server-derived tenant key cannot be accounted
#: for fairly, so it is refused rather than dropped into a shared bucket.
#: Fail-closed, same trust boundary as NO_AUTHENTICATED_TENANT.
UNKEYED_TENANT_HANDLE = "unkeyed_tenant_handle"

#: A cap the operator typed wrong is an UNKNOWN operating capacity. The
#: worker refuses by name rather than silently substituting a default --
#: an operator who typed "3O" must not be told the box is running at 3.
WORKER_CAP_MALFORMED = "worker_cap_malformed"
#: A profile naming a box that cannot host a writer child at all.
WORKER_PROFILE_UNSUPPORTED = "worker_profile_unsupported"
WORKER_PROFILE_UNKNOWN = "worker_profile_unknown"

#: Phase 1's named refusal, mirrored here so the builder's isolation
#: boundary uses the same reason string as the runtime seam.
NO_AUTHENTICATED_TENANT = "no_authenticated_tenant"

# ---------------------------------------------------------------------------
# THE MEMORY BUDGET -- recompute these three numbers before raising a cap.
# ---------------------------------------------------------------------------
# The live cerebrumdev-backend runs on Render plan 1c-2g (1 vCPU / 2 GB,
# numInstances=1, autoscaling=None -- read from the Render API 2026-09-16).
#
#   BASE_MB     = 500  uvicorn + FastAPI + chromadb + the app import graph,
#                      resident before any build starts (~400-500 MB
#                      measured; 500 is the pessimistic figure).
#   HEADROOM_MB = 150  Render overhead, page cache, and the per-build Python
#                      objects the build THREAD itself holds (workspace tree,
#                      blueprint, ledger buffers).
#   PER_JOB_MB  = 400  One `codewhale exec` Node child at peak RSS. THIS IS
#                      THE NUMBER THAT MOVES THE TABLE MOST and it is an
#                      ESTIMATE, not a measurement -- a Node agent CLI with
#                      loaded tool/context state typically lands near 300 MB.
#                      MEASURE IT before raising any cap.
#
#   process_cap = min( floor((RAM_MB - BASE_MB - HEADROOM_MB) / PER_JOB_MB),
#                      vCPU * 8 )
#
# The CPU bound is 8 jobs/vCPU because these children are almost entirely
# blocked on DeepSeek HTTP -- the local CPU cost is JSON parsing and file
# writes. Memory is the binding constraint on every row but the largest.
BASE_MB = 500
HEADROOM_MB = 150
PER_JOB_MB = 400

#: 2048 MB - 500 - 150 = 1398; 1398 / 400 = 3. The CPU bound (1 x 8) is
#: slack. THREE is what a 1c-2g box can honestly carry, and it is the CODE
#: default because render.yaml is not an applied blueprint (see its header)
#: -- the default is what production actually receives on the next deploy,
#: with zero dashboard action.
DEFAULT_PROCESS_CAP = 3
#: Fairness floor. One tenant, one concurrent build on a 3-slot box: two
#: other accounts can always get in. Per-tenant caps grow far more slowly
#: than the process cap on purpose -- see WORKER_PROFILES.
DEFAULT_TENANT_CAP = 1
DEFAULT_WORKER_TIMEOUT_S = 1800.0

#: Kept for the existing operator contract: this env var has always named
#: the PROCESS cap and still does.
PROCESS_CAP_ENV = "FACTORY_CODEWHALE_WORKER_CAP"
TENANT_CAP_ENV = "FACTORY_CODEWHALE_TENANT_CAP"
#: THE SINGLE UPGRADE KNOB. Moving from a 3-slot box to a 50-tenant box is
#: this one env var plus a Render resize. No code edit.
PROFILE_ENV = "FACTORY_WORKER_PROFILE"

#: Render plan -> (process cap, per-tenant cap). DATA, not code: the
#: upgrade path is config-only.
#:
#:   plan      RAM      vCPU  memory bound              CPU bound  PROCESS  TENANT
#:   ------------------------------------------------------------------------------
#:   starter    512 MB  0.5   (512-650)/400   -> < 0    4          UNSUPPORTED
#:   1c-2g     2048 MB  1     (2048-650)/400  = 3       8          3        1  <- LIVE
#:   2c-4g     4096 MB  2     (4096-650)/400  = 8      16          8        1
#:   4c-8g     8192 MB  4     (8192-650)/400  = 18     32         18        1
#:   4c-16g   16384 MB  4     (16384-650)/400 = 39     32         32        2
#:   8c-32g   32768 MB  8     (32768-650)/400 = 80     64         64        4  <- 50-TENANT
#:
#: Per-tenant rule: max(1, min(4, process_cap // 16)) -- no account may hold
#: more than ~6% of a large box, and on small boxes the floor of 1 governs.
#: Fairness is the point: at 8c-32g, 64 slots with per-tenant 4 still lets
#: 50 distinct tenants build at once, and no single tenant can take the box.
#:
#: THE 50-TENANT ROW, explicitly: 50 concurrent tenants at per-tenant 1 needs
#: process_cap >= 50, i.e. 50*400 + 650 = 20,650 MB. 4c-16g gives 39 -- NOT
#: ENOUGH. 8c-32g is the first plan that clears it (64 feasible vs 50 needed).
#: If PER_JOB_MB is MEASURED at 250 instead of 400, 50*250+650 = 13,150 MB and
#: 4c-16g becomes sufficient -- which is exactly why PER_JOB_MB is the number
#: called out for recomputation.
#:
#: DESIGN capacity vs OPERATING capacity: the machinery genuinely serves 50
#: distinct tenant keys (proved by the stub tests, which drive 50 real
#: bind_tenant_store digests through acquire/release). The OPERATING cap on
#: today's box stays 3/1. The code never claims capacity the box cannot
#: serve -- 50 is reachable only by naming a plan that has the RAM for it.
#:
#: ``starter`` maps to None deliberately: 512 MB does not fit BASE_MB, let
#: alone a Node child. It resolves to a NAMED REFUSAL rather than quietly
#: emitting a cap the box cannot serve. It is listed so nobody infers a
#: value for it from the pattern -- and because render.yaml declared exactly
#: this plan until this change.
WORKER_PROFILES: Dict[str, Optional[Tuple[int, int]]] = {
    "starter": None,
    "1c-2g": (3, 1),
    "2c-4g": (8, 1),
    "4c-8g": (18, 1),
    "4c-16g": (32, 2),
    "8c-32g": (64, 4),
}

#: The profile the live box runs, and the value render.yaml declares.
LIVE_PROFILE = "1c-2g"
#: The smallest profile that can serve 50 concurrent tenants.
FIFTY_TENANT_PROFILE = "8c-32g"


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


def _cap_from_env(name: str) -> Optional[int]:
    """An explicit operator override, or None when the var is unset.

    A malformed value is a NAMED refusal, never a silent fallback. The old
    ``int(os.getenv(...) or 1)`` had three failure modes that become
    platform-wide outages once this gates 50 tenants: "0" slipped past the
    ``or`` guard (which only catches the EMPTY string) and refused every
    job including the first; a negative did the same; and a typo raised a
    bare ValueError from inside the slot lock -- an unnamed crash, not a
    named refusal. Silently substituting the default is no better: an
    operator who typed "3O" would be told the box runs at 3 when nobody
    chose 3. Unknown capacity is refused, loudly.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        value = int(text)
    except ValueError as exc:
        raise WorkerError(
            f"{WORKER_CAP_MALFORMED}: {name}={raw!r} is not an integer — "
            "refusing to guess a concurrency cap"
        ) from exc
    if value < 1:
        raise WorkerError(
            f"{WORKER_CAP_MALFORMED}: {name}={raw!r} must be >= 1 — a zero "
            "or negative cap is an outage, not a configuration"
        )
    return value


def worker_profile() -> str:
    """The single upgrade knob: a Render plan slug from WORKER_PROFILES."""
    return os.getenv(PROFILE_ENV, "").strip()


def _profile_caps() -> Optional[Tuple[int, int]]:
    """(process, tenant) for the named profile, or None when unset.

    An unknown or unsupported profile is a NAMED refusal: the operator
    named a box this code has no honest numbers for, and inventing one is
    exactly the "claim a capacity the box cannot serve" failure.
    """
    name = worker_profile()
    if not name:
        return None
    if name not in WORKER_PROFILES:
        raise WorkerError(
            f"{WORKER_PROFILE_UNKNOWN}: {PROFILE_ENV}={name!r} is not a known "
            f"plan — known: {', '.join(sorted(WORKER_PROFILES))}"
        )
    caps = WORKER_PROFILES[name]
    if caps is None:
        raise WorkerError(
            f"{WORKER_PROFILE_UNSUPPORTED}: {PROFILE_ENV}={name!r} is 0.5 vCPU "
            f"/ 512 MB — it does not fit the app's own {BASE_MB} MB resident "
            f"footprint, let alone a {PER_JOB_MB} MB writer child. Resize "
            "before pointing the profile here."
        )
    return caps


def worker_process_cap() -> int:
    """Total jobs in flight allowed on this instance, across all tenants.

    PRECEDENCE, highest first:
      1. FACTORY_CODEWHALE_WORKER_CAP  (explicit operator override)
      2. FACTORY_WORKER_PROFILE        (the upgrade knob)
      3. DEFAULT_PROCESS_CAP           (the honest 1c-2g default)
    """
    explicit = _cap_from_env(PROCESS_CAP_ENV)
    if explicit is not None:
        return explicit
    caps = _profile_caps()
    if caps is not None:
        return caps[0]
    return DEFAULT_PROCESS_CAP


def worker_tenant_cap() -> int:
    """Jobs in flight allowed for ONE bound tenant. Same precedence."""
    explicit = _cap_from_env(TENANT_CAP_ENV)
    if explicit is not None:
        return explicit
    caps = _profile_caps()
    if caps is not None:
        return caps[1]
    return DEFAULT_TENANT_CAP


def worker_cap() -> int:
    """Back-compat alias: this name has always meant the PROCESS cap."""
    return worker_process_cap()


def worker_timeout_s() -> float:
    return float(
        os.getenv("FACTORY_CODEWHALE_WORKER_TIMEOUT_S", str(DEFAULT_WORKER_TIMEOUT_S))
    )


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


# ---------------------------------------------------------------------------
# MULTI-INSTANCE SEAM.
#
# This counter is CORRECT only while the service runs exactly ONE instance.
# Verified against the live Render API 2026-09-16: cerebrumdev-backend
# numInstances=1, autoscaling=None. Builds are daemon THREADS in this one
# process (build_jobs.py, threading.Thread(target=_run)), so a
# threading.Lock around in-memory counters is the whole of the problem.
#
# THE CONDITION THAT FORCES THE SWAP: numInstances > 1, or Render
# autoscaling enabled. At that moment each instance keeps its own
# _process_active and independently admits up to the process cap, so the
# real ceiling silently becomes numInstances x process_cap and the memory
# budget above is wrong by that factor -- in the direction that OOMs the
# box. Per-tenant fairness breaks the same way: a tenant capped at 1 gets
# numInstances concurrent builds. Nothing raises an error; the caps just
# quietly stop meaning what they say.
#
# DO NOT implement shared state until that condition holds. A network
# round-trip per slot acquire is a new failure mode for zero benefit at one
# instance. When it does hold the substrate is already provisioned --
# cerebrumdev-redis in render.yaml, wired to REDIS_URL -- so the swap is
# implementation only: no new dependency, no new infrastructure.
#
# TO SWAP: implement acquire/release/snapshot with the same signatures and
# semantics (atomic check-and-increment; decrement-and-delete-at-zero;
# release must be exception-safe and must not leak a slot if the holder
# dies -- a TTL on the shared entry, which the in-process version does not
# need because the finally block cannot be skipped) and call
# set_slot_counter(). No caller changes.
#
# The seam is a PROTOCOL, not an abstract base class: nothing is imported
# to define it, so "no new dependency" holds literally.
# ---------------------------------------------------------------------------


class InProcessSlotCounter:
    """Two-axis concurrency accounting for ONE worker process.

    process axis -- host protection: total in flight on this instance.
    tenant axis  -- fairness: in flight for ONE bound tenant.

    ONE lock guards both. EVERY mutation happens inside it, cleanup
    included, and the per-tenant entry is POPPED at zero in the same
    critical section as the decrement -- never read-then-delete across two
    acquisitions, which at 50 tenants would race a concurrent acquire into
    resurrecting a half-deleted entry. The dict is therefore bounded by the
    number of tenants CURRENTLY holding slots, never by the number of
    tenants ever seen.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process_active = 0
        # A plain dict, deliberately not a defaultdict: a defaultdict
        # materialises an entry on every READ, including the read that is
        # about to refuse, so a refusal storm would grow the map as fast as
        # the successes.
        self._tenant_active: Dict[str, int] = {}

    def acquire(self, key: str, *, process_cap: int, tenant_cap: int) -> None:
        """Take one slot for ``key`` or raise a named WorkerError.

        CHECK ORDER is process first, then tenant, and that is deliberate:
        the process cap is the host-protection invariant. If the instance
        is full the job is refused regardless of whose it is, so reporting
        "this user is at their limit" when the truth is "the box is full"
        would send an operator to the wrong place. Process exhaustion is
        the page-worthy condition and wins the message.
        """
        short = key[:8]
        with self._lock:
            active = self._process_active
            held = self._tenant_active.get(key, 0)
            if active >= process_cap:
                # No mutation on the refusal path.
                raise WorkerError(
                    f"{WORKER_CONCURRENCY_CAPPED}: {PROCESS_SLOTS_EXHAUSTED} "
                    f"scope=process — {active}/{process_cap} job slots in "
                    f"flight on this instance across all tenants (tenant "
                    f"{short} holds {held}/{tenant_cap}) — job refused, never "
                    f"silently run and never silently queued. Raise "
                    f"{PROCESS_CAP_ENV} only with the memory budget "
                    f"recomputed, or move {PROFILE_ENV} to a larger plan."
                )
            if held >= tenant_cap:
                raise WorkerError(
                    f"{WORKER_CONCURRENCY_CAPPED}: {TENANT_SLOTS_EXHAUSTED} "
                    f"scope=tenant — tenant {short} holds {held}/{tenant_cap} "
                    f"of its own concurrent build slots; this instance has "
                    f"{active}/{process_cap} in flight, so other tenants are "
                    f"unaffected — job refused, never silently run and never "
                    f"silently queued."
                )
            # Both mutations together, LAST. The increment is the final
            # statement of the critical section and `try:` opens immediately
            # after it in worker_job_slot, so no path can decrement a slot it
            # never incremented.
            self._process_active = active + 1
            self._tenant_active[key] = held + 1

    def release(self, key: str) -> None:
        """Give the slot back. Mirrors acquire; runs from a finally block."""
        with self._lock:
            self._process_active -= 1
            remaining = self._tenant_active.get(key, 0) - 1
            if remaining > 0:
                self._tenant_active[key] = remaining
            else:
                # CLEANUP INSIDE THE LOCK. This pop is what keeps 50
                # churning tenants from leaving 50 zero-valued entries
                # behind forever.
                self._tenant_active.pop(key, None)

    def snapshot(self) -> Dict[str, Any]:
        """Observability + the leak assertion. A copy, never the live map."""
        with self._lock:
            return {
                "total": self._process_active,
                "by_tenant": dict(self._tenant_active),
            }


_COUNTER: Any = InProcessSlotCounter()


def set_slot_counter(counter: Any) -> Any:
    """Install a slot-counter backend; returns the one replaced.

    The multi-instance swap point (see the seam note above), and the way a
    test installs a clean counter instead of reaching into module globals.
    """
    global _COUNTER
    previous = _COUNTER
    _COUNTER = counter
    return previous


def slot_counter() -> Any:
    return _COUNTER


def worker_slots_snapshot() -> Dict[str, Any]:
    """``{"total": int, "by_tenant": {key: int}}`` for the live counter."""
    return _COUNTER.snapshot()


_TENANT_KEY_ATTR = "tenant_key"


def _tenant_slot_key(tenant_store: Any) -> str:
    """The slot bucket for a BOUND handle. Never a client-supplied name.

    Production always takes the first branch: the handle is a
    TenantStoreBinding (tenant_bind.TenantStoreBinding) whose ``tenant_key``
    is a server-side sha256 digest of the AUTHENTICATED identity
    (tenant_bind.bind_tenant_store). The caller cannot choose another
    tenant's bucket for the same reason it cannot choose another tenant's
    store dir: it never supplies the string. Same handle, same boundary,
    same trust decision as _require_bound_tenant -- no second decision is
    introduced here.

    Deliberately NOT read: ``tenant_id``, ``digest``, ``name`` or any other
    attribute. Those are attacker-shaped names on an arbitrary object.

    A handle with no tenant_key is REFUSED BY NAME rather than dropped into
    a shared or anonymous bucket. Fail-closed: an unkeyed handle cannot be
    accounted for fairly, and a shared fallback bucket would silently
    collapse every unkeyed caller onto one key -- which would make a
    50-tenant test pass while proving nothing. The only handles production
    ever passes are TenantStoreBinding or None (build_jobs binds it,
    runner seeds ctx.state, roles_handlers hands it here), so this refusal
    is unreachable from the live path by construction.
    """
    key = getattr(tenant_store, _TENANT_KEY_ATTR, None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    raise WorkerError(
        f"{UNKEYED_TENANT_HANDLE}: the bound handle carries no server-derived "
        f"{_TENANT_KEY_ATTR} — refusing to account a build against a shared "
        "slot bucket"
    )


@contextmanager
def worker_job_slot(tenant_store: Any) -> Iterator[None]:
    """Acquire one concurrency slot for this tenant's job.

    Phase 1 applies to the builder: the store must already be BOUND for
    this job (resolved from the authenticated principal). An unbound job
    refuses with ``no_authenticated_tenant`` — P5's mutation runs the
    worker unbound and the suite goes RED.

    Two caps gate the job. The PROCESS cap protects the host; the TENANT
    cap keeps one account from taking the box. Both refusals are named and
    distinguishable; neither queues.
    """
    # The tenant boundary comes FIRST and outside the lock: it raises and
    # touches no counter state, so there is nothing to unwind, and an
    # unbound handle never reaches the counter at all.
    _require_bound_tenant(tenant_store)
    key = _tenant_slot_key(tenant_store)
    # Read each cap ONCE, outside the lock, into a local. os.environ is
    # mutated at runtime elsewhere in this process, so two reads are not
    # guaranteed to agree -- and a refusal message that reports a cap which
    # never gated anything sends an operator chasing a number that does not
    # exist. These same locals make the decision AND the message.
    process_cap = worker_process_cap()
    tenant_cap = worker_tenant_cap()
    _COUNTER.acquire(key, process_cap=process_cap, tenant_cap=tenant_cap)
    try:
        yield
    finally:
        # An exception inside the yield still releases the slot.
        _COUNTER.release(key)


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


def _child_env(session_id: Optional[str]) -> Dict[str, str]:
    """The environment ONE writer child runs under.

    Every child used to inherit the live process environment by reference
    at fork, with no ``env=`` argument. That is harmless at one concurrent
    build and a cross-tenant bleed at three: build-scoped keys are written
    into ``os.environ`` from inside per-build threads, so tenant B's writer
    could be spawned carrying tenant A's session id. Raising the process
    cap is what activates that, so the snapshot ships WITH the cap raise.

    The snapshot is taken ONCE, here, at job start, and the build-scoped
    keys are stamped from THIS job's own identity rather than inherited
    from whatever the process global happens to hold at fork time.
    """
    env = dict(os.environ)
    sid = str(session_id or "").strip()
    if sid:
        env["FACTORY_SESSION_ID"] = sid
        env["FACTORY_CLI_PIVOT_SESSION_ID"] = sid
    return env


def run_worker_job(
    prompt: str,
    checkout_dir: Path | str,
    *,
    tenant_store: Any = None,
    timeout_s: Optional[float] = None,
    session_id: Optional[str] = None,
    progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> WorkerReceipt:
    """Run one headless CodeWhale exec — non-interactive, JSON summary.

    T5.1: no approval prompt may hang the worker; the CLI's --auto mode is
    the documented non-interactive automation path. The job runs INSIDE a
    worker slot; beyond either cap it refuses, never queues-and-forgets.

    ``session_id`` is this build's own identity, threaded from the runner
    state. It stamps the child environment so a concurrent build cannot
    hand its session id to this one.
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

        # E4: persist exactly what the coder was told and how it was
        # invoked, so "what did the writer receive" never needs a source
        # read or an SSH session again. The api key is scrubbed from the
        # persisted argv.
        sanitized = [
            "[redacted]" if (a == api_key and api_key) else a for a in argv
        ]
        try:
            (cwd / "docs").mkdir(parents=True, exist_ok=True)
            (cwd / "docs" / "writer_prompt.txt").write_text(
                prompt, encoding="utf-8"
            )
            (cwd / "docs" / "writer_argv.json").write_text(
                json.dumps({"argv": sanitized}, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:  # never fail the build on audit persistence
            logger.exception("writer prompt/argv persistence failed")

        # E3 part 1: the dispatch line an operator can tail on Render.
        logger.info(
            "codewhale writer dispatch: cli=%s provider=%s session=%s timeout=%ss",
            cli,
            worker_provider(),
            str(session_id or "")[:12] or "-",
            timeout,
        )
        from app.factory.build.sanitize import sanitize_for_status

        # The writer pass narrates itself: the CLI's agent-loop progress
        # (its log file) and its stderr are streamed line by line into the
        # progress callback — the role relays throttled NOTEs to the ledger
        # so the Floor shows what the writer is doing instead of "quiet
        # for N min". Persisted to docs/writer_progress.jsonl too.
        # stdout stays RAW and separate: it carries the final JSON summary
        # (merging stderr in broke the parse — live run4
        # sess_620b8581fb224bea).
        tailer = _cli_log_tailer()
        progress_path = cwd / "docs" / "writer_progress.jsonl"
        try:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_handle = progress_path.open("a", encoding="utf-8")
        except OSError:
            progress_handle = None
        stdout_lines: List[str] = []
        relay_queue: "queue.Queue[str]" = queue.Queue()

        def _relay(raw: str) -> None:
            line = sanitize_for_status(raw)[:400]
            if not line.strip():
                return
            if progress_handle is not None:
                try:
                    progress_handle.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "line": line,
                            }
                        )
                        + "\n"
                    )
                    progress_handle.flush()
                except OSError:
                    pass
            if progress is not None:
                try:
                    progress(line, {"tool": _tool_hint(line)})
                except Exception:  # noqa: BLE001 — telemetry never fails the build
                    pass

        def _stdout_reader() -> None:
            try:
                assert proc.stdout is not None
                for raw in iter(proc.stdout.readline, ""):
                    if raw.strip():
                        stdout_lines.append(raw.rstrip("\r\n"))
            except (OSError, ValueError):  # closed pipe on kill
                pass

        def _stderr_reader() -> None:
            try:
                assert proc.stderr is not None
                for raw in iter(proc.stderr.readline, ""):
                    if raw.strip():
                        relay_queue.put(raw.rstrip("\r\n"))
            except (OSError, ValueError):  # closed pipe on kill
                pass

        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=_child_env(session_id),
            )
        except OSError as exc:
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: could not start {cli}: {exc}"
            ) from exc

        stdout_reader = threading.Thread(target=_stdout_reader, daemon=True)
        stderr_reader = threading.Thread(target=_stderr_reader, daemon=True)
        stdout_reader.start()
        stderr_reader.start()
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        proc.kill()
                        proc.wait()
                        raise WorkerError(
                            f"{WORKER_TIMED_OUT}: headless job exceeded "
                            f"{timeout}s"
                        )
                    # Drain relays on THIS thread: ledger notes stay
                    # single-writer (the build thread is blocked here).
                    while True:
                        try:
                            _relay(relay_queue.get_nowait())
                        except queue.Empty:
                            break
                    tailer.pump(_relay)
        finally:
            # Drain the remainder so no authored evidence is lost.
            stdout_reader.join(timeout=5)
            stderr_reader.join(timeout=5)
            while True:
                try:
                    _relay(relay_queue.get_nowait())
                except queue.Empty:
                    break
            tailer.pump(_relay)
            if progress_handle is not None:
                try:
                    progress_handle.close()
                except OSError:
                    pass
        returncode = proc.returncode
        if returncode != 0:
            tail = "\n".join(stdout_lines)[-400:]
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: exit {returncode}: {tail}"
            )
        # The summary is the CLI's final JSON on stdout. Parse exactly as
        # before the streaming change: the last line, then the whole raw
        # buffer (never the sanitised relay copy).
        payload = None
        candidates = [stdout_lines[-1]] if stdout_lines else []
        candidates.append("\n".join(stdout_lines))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
                break
            except ValueError:
                continue
        if payload is None:
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: non-JSON summary from the CLI"
            )
        receipt = WorkerReceipt(
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
        # E3 part 2: the verdict line.
        logger.info(
            "codewhale writer receipt: status=%s reason=%s provider=%s "
            "model=%s tools=%d error=%s",
            receipt.status,
            receipt.termination_reason or "-",
            receipt.provider,
            receipt.model,
            len(receipt.tools),
            receipt.error_category or "-",
        )
        return receipt


def _cli_log_tailer() -> Any:
    """A fresh tailer for one worker pass."""
    return _CliLogTailer()


def _tool_hint(line: str) -> str:
    """Best-effort classification of one CLI progress line for the Floor."""
    text = (line or "").lower()
    if "engine turn" in text:
        return "agent-step"
    for name in (
        "write",
        "edit",
        "create",
        "delete",
        "bash",
        "exec",
        "read",
        "search",
        "grep",
        "test",
        "pytest",
    ):
        if name in text:
            return name
    return ""


class _CliLogTailer:
    """Polls the CLI's own log file for new lines during a headless pass.

    The codewhale CLI writes its agent-loop progress (e.g. ``engine turn
    completion settled status=...``) to ``~/.codewhale/logs/*.log`` rather
    than stdout. Tailing it turns the 30-minute silent pass into a stream
    of named steps. Best-effort: no log dir is never an error.
    """

    def __init__(self) -> None:
        self._dir: Optional[Path] = None
        self._file: Optional[Path] = None
        self._offset = 0
        self._last: Optional[float] = None
        for candidate in (
            Path.home() / ".codewhale" / "logs",
            Path(os.getenv("CODWHALE_HOME", "") or "") / "logs",
        ):
            try:
                if candidate.is_dir():
                    self._dir = candidate
                    break
            except OSError:
                continue

    def _pick(self) -> None:
        if self._dir is None:
            return
        try:
            candidates = sorted(
                (p for p in self._dir.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
        if not candidates:
            return
        newest = candidates[0]
        if newest != self._file:
            self._file = newest
            try:
                # The CLI rotates per run; only new lines from this file.\n"
                self._offset = newest.stat().st_size
            except OSError:
                self._offset = 0

    def pump(self, relay: Any) -> None:
        """Relay lines appended since the last pump. No-op when no logs."""
        if self._dir is None:
            return
        if self._file is None:
            self._pick()
            return
        try:
            stat = self._file.stat()
        except OSError:
            return
        if stat.st_size < self._offset:
            # Truncated/rotated: start from the current end.\n"
            self._offset = stat.st_size
            return
        if stat.st_size == self._offset:
            return
        try:
            with self._file.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError:
            return
        for raw in chunk.splitlines():
            if raw.strip():
                relay(_cli_log_line(raw))
        # Rotation check: the CLI may have opened a new file.\n"
        try:
            newest = max(
                (p for p in self._dir.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                default=None,
            )
        except OSError:
            newest = None
        if newest is not None and newest != self._file:
            self._file = None
            self._offset = 0


def _cli_log_line(raw: str) -> str:
    """Strip the CLI log's timestamp/level prefix for Floor readability."""
    text = raw.strip()
    parts = text.split(" ", 2)
    if len(parts) == 3 and parts[0].startswith("20") and "T" in parts[0]:
        text = parts[2]
    return "codewhale log: " + text
