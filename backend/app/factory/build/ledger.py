"""Build ledger — the durable, resumable record of one manufacturing run.

A two-hour build cannot be a function call whose only output is its return
value. It has to survive an interrupt, be auditable afterwards, and answer
"what did each role actually do" without anyone having to trust a summary.

The ledger is append-only JSONL for that reason: a crash mid-phase leaves
every prior line intact and readable, where a rewritten JSON document would
leave a truncated file and no history. Nothing here mutates or deletes a
past event -- a phase that is re-run appends a new event, and
:meth:`BuildLedger.completed_roles` reads the latest verdict per role.

The ledger is also the registrar's source of truth. ``CLONE`` events record
which block landed in which platform at which commit, so the Store Manager
can answer "what did this platform take from the store, and has any of it
gone stale against store head" by reading ledgers rather than by re-scanning
delivered artifacts.

Only the orchestrator writes here. Roles do not -- a role that can edit the
record of its own gate is the same hole that
:mod:`app.factory.build.authority` closes for source files.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Set

logger = logging.getLogger(__name__)

_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_ledger(path: Path):
    """Process + inter-process lock around a ledger append."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(path)
    with thread_lock:
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            flocked = False
            try:
                import fcntl

                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                flocked = True
            except ImportError:
                pass
            try:
                yield
            finally:
                if flocked:
                    import fcntl

                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

from app.factory.build.authority import BUILD_PHASES, BuildRole

LEDGER_SCHEMA = "build_ledger.v1"


class EventKind(str, Enum):
    RUN_STARTED = "RUN_STARTED"
    PHASE_STARTED = "PHASE_STARTED"
    GATE_PASSED = "GATE_PASSED"
    GATE_FAILED = "GATE_FAILED"
    PHASE_ABORTED = "PHASE_ABORTED"
    #: A writer<->tester round trip. Recorded so a run that converged after
    #: eleven attempts does not read like one that passed first try.
    REWORK = "REWORK"
    CLONE = "CLONE"
    NOTE = "NOTE"
    #: Reopens TESTER + STORE_MANAGER after a code-phase SUCCESS so the same
    #: workspace can run the Store-green / ``pytest -m pilot`` cycle. Does
    #: not wipe WRITER artifacts or start a second product.
    PILOT_OPENED = "PILOT_OPENED"
    #: Terminal verdicts. Exactly one of these ends a finished run, so the
    #: outcome is readable from the ledger alone without re-deriving it from
    #: phase events -- and a run that stopped without one is, correctly, not
    #: a run that succeeded.
    RUN_SUCCEEDED = "RUN_SUCCEEDED"
    RUN_FAILED = "RUN_FAILED"


#: Kinds that end a phase. The last terminal event for a role is its verdict.
TERMINAL_KINDS = frozenset(
    {EventKind.GATE_PASSED, EventKind.GATE_FAILED, EventKind.PHASE_ABORTED}
)


class LedgerError(RuntimeError):
    """The ledger on disk cannot be trusted for the run being attempted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class BuildEvent:
    seq: int
    ts: str
    kind: EventKind
    role: Optional[BuildRole] = None
    detail: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind.value,
            "role": self.role.value if self.role else None,
            "detail": self.detail,
            "payload": dict(self.payload),
        }

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> "BuildEvent":
        role = raw.get("role")
        return BuildEvent(
            seq=int(raw["seq"]),
            ts=str(raw.get("ts", "")),
            kind=EventKind(raw["kind"]),
            role=BuildRole(role) if role else None,
            detail=str(raw.get("detail", "")),
            payload=dict(raw.get("payload") or {}),
        )


class BuildLedger:
    """Append-only run record at *path*.

    ``inputs_hash`` pins the run to the blueprint that started it. Resuming
    against a different blueprint is refused rather than silently continuing
    a build whose earlier phases were made from different inputs.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._clock = clock

    # -- reading ---------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def events(self) -> List[BuildEvent]:
        if not self.path.is_file():
            return []
        out: List[BuildEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(BuildEvent.from_json(json.loads(line)))
                except (ValueError, KeyError) as exc:
                    # A half-written final line is the expected shape of a
                    # crash; anything earlier means real corruption.
                    raise LedgerError(
                        f"{self.path}:{lineno} is not a readable ledger event: {exc}"
                    ) from exc
        return out

    def _next_seq(self) -> int:
        events = self.events()
        return (events[-1].seq + 1) if events else 1

    # -- writing ---------------------------------------------------------

    def append(
        self,
        kind: EventKind,
        *,
        role: Optional[BuildRole | str] = None,
        detail: str = "",
        payload: Optional[Mapping[str, Any]] = None,
    ) -> BuildEvent:
        event = BuildEvent(
            seq=self._next_seq(),
            ts=self._clock(),
            kind=EventKind(kind),
            role=BuildRole(role) if role else None,
            detail=detail,
            payload=dict(payload or {}),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_json(), sort_keys=True) + "\n"
        # One write plus fsync per event, under a file lock so two workers
        # cannot interleave JSONL lines.
        with _exclusive_ledger(self.path):
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
        return event

    def start_run(self, *, product_id: str, inputs_hash: str) -> BuildEvent:
        return self.append(
            EventKind.RUN_STARTED,
            detail=f"build of {product_id}",
            payload={
                "schema": LEDGER_SCHEMA,
                "product_id": product_id,
                "inputs_hash": inputs_hash,
            },
        )

    def open_pilot_cycle(
        self,
        *,
        reason: str = "code-phase SUCCESS; opening Store-green cycle",
    ) -> BuildEvent:
        """Reopen TESTER + STORE_MANAGER on an existing workspace.

        Append-only: COLLECTOR/CLONER/WRITER verdicts stay. A later
        ``RUN_SUCCEEDED`` with ``cycle=pilot`` is what ``pilot_ready`` reads.
        """
        return self.append(
            EventKind.PILOT_OPENED,
            detail=reason,
            payload={"cycle": "pilot"},
        )

    def record_clone(
        self,
        *,
        block_id: str,
        source_commit: str,
        store_repo: str,
        vendored_path: str,
    ) -> BuildEvent:
        """Register one block taken from the store into this platform."""
        return self.append(
            EventKind.CLONE,
            role=BuildRole.CLONER,
            detail=f"cloned {block_id}@{source_commit[:12]}",
            payload={
                "block_id": block_id,
                "source_commit": source_commit,
                "store_repo": store_repo,
                "vendored_path": vendored_path,
            },
        )

    # -- derived state ---------------------------------------------------

    def inputs_hash(self) -> Optional[str]:
        for event in self.events():
            if event.kind is EventKind.RUN_STARTED:
                return str(event.payload.get("inputs_hash") or "") or None
        return None

    def completed_roles(self) -> Set[BuildRole]:
        """Roles whose most recent *completed* attempt was a pass.

        Latest-verdict-wins, so a role that failed, was reworked and then
        passed counts as complete, and one that passed and was later aborted
        does not.

        A role whose last event is ``PHASE_STARTED`` is **running, not
        complete**, even if an earlier attempt passed. That distinction is the
        difference between resuming correctly and resuming onto rubble: a
        process killed mid-WRITER leaves files from two different attempts,
        and the agent picks different entity names each call, so the halves do
        not compose. Reading the stale GATE_PASSED would resume at TESTER and
        test a torn workspace.
        """
        state: Dict[BuildRole, EventKind] = {}
        for event in self.events():
            if event.kind is EventKind.PILOT_OPENED:
                # Code-phase TESTER used ``not pilot``; STORE_MANAGER applied
                # no store op. Both must run again. WRITER stays complete
                # until a failed pilot gate sends a rework.
                state.pop(BuildRole.TESTER, None)
                state.pop(BuildRole.STORE_MANAGER, None)
                continue
            if not event.role:
                continue
            if event.kind is EventKind.PHASE_STARTED or event.kind in TERMINAL_KINDS:
                state[event.role] = event.kind
        return {r for r, k in state.items() if k is EventKind.GATE_PASSED}

    def interrupted_role(self) -> Optional[BuildRole]:
        """The role that started and never finished, if the run was killed."""
        state: Dict[BuildRole, EventKind] = {}
        for event in self.events():
            if not event.role:
                continue
            if event.kind is EventKind.PHASE_STARTED or event.kind in TERMINAL_KINDS:
                state[event.role] = event.kind
        for role, kind in state.items():
            if kind is EventKind.PHASE_STARTED:
                return role
        return None

    def resume_point(self) -> Optional[BuildRole]:
        """The first phase not yet passed, or None when the run is finished."""
        done = self.completed_roles()
        for phase in BUILD_PHASES:
            if phase not in done:
                return phase
        return None

    def assert_resumable(self, *, inputs_hash: str) -> None:
        """Refuse to continue a run whose inputs changed underneath it."""
        recorded = self.inputs_hash()
        if recorded is None:
            return
        if recorded != inputs_hash:
            raise LedgerError(
                "cannot resume: ledger was started from inputs "
                f"{recorded[:12]} but this run supplies {inputs_hash[:12]} — "
                "start a fresh build rather than mixing them"
            )

    def clones(self) -> List[Dict[str, Any]]:
        """Every block this platform took from the store, latest per block."""
        latest: Dict[str, Dict[str, Any]] = {}
        for event in self.events():
            if event.kind is EventKind.CLONE:
                bid = str(event.payload.get("block_id") or "")
                if bid:
                    latest[bid] = {**dict(event.payload), "ts": event.ts}
        return [latest[k] for k in sorted(latest)]

    def terminal_event(self) -> Optional[BuildEvent]:
        """The run's recorded verdict, or None if it never finished.

        None is the honest answer for a killed run: absence of a verdict is
        not success, and callers must not infer one from "no failures seen".
        A ``PILOT_OPENED`` after the last terminal reopens the run.
        """
        last: Optional[BuildEvent] = None
        for event in self.events():
            if event.kind in (EventKind.RUN_SUCCEEDED, EventKind.RUN_FAILED):
                last = event
            elif event.kind is EventKind.PILOT_OPENED:
                last = None
        return last

    def succeeded(self) -> bool:
        event = self.terminal_event()
        return event is not None and event.kind is EventKind.RUN_SUCCEEDED

    def code_phase_succeeded(self) -> bool:
        """True once any RUN_SUCCEEDED was recorded (code-phase 5/5 counts)."""
        return any(e.kind is EventKind.RUN_SUCCEEDED for e in self.events())

    def pilot_cycle_open(self) -> bool:
        """True when a PILOT_OPENED is the latest cycle marker (no later SUCCESS)."""
        last_pilot = last_success = None
        for event in self.events():
            if event.kind is EventKind.PILOT_OPENED:
                last_pilot = event
            if event.kind is EventKind.RUN_SUCCEEDED:
                last_success = event
        return last_pilot is not None and (
            last_success is None or last_pilot.seq > last_success.seq
        )

    def pilot_ready(self) -> bool:
        """True only after a SUCCESS that closed a pilot cycle."""
        event = self.terminal_event()
        if event is None or event.kind is not EventKind.RUN_SUCCEEDED:
            return False
        payload = event.payload or {}
        return payload.get("cycle") == "pilot" or bool(payload.get("pilot_ready"))

    def rework_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self.events():
            if event.kind is EventKind.REWORK and event.role:
                counts[event.role.value] = counts.get(event.role.value, 0) + 1
        return counts

    def summary(self) -> Dict[str, Any]:
        events = self.events()
        done = self.completed_roles()
        resume = self.resume_point()
        return {
            "schema_version": LEDGER_SCHEMA,
            "path": str(self.path),
            "events": len(events),
            "started": events[0].ts if events else None,
            "last_event": events[-1].ts if events else None,
            "inputs_hash": self.inputs_hash(),
            "completed_roles": [p.value for p in BUILD_PHASES if p in done],
            "resume_point": resume.value if resume else None,
            "complete": resume is None,
            "rework": self.rework_counts(),
            "clones": self.clones(),
            "outcome": (
                self.terminal_event().kind.value if self.terminal_event() else None
            ),
        }


def iter_ledgers(root: Path | str, *, filename: str = "build_ledger.jsonl") -> Iterator[BuildLedger]:
    """Every build ledger under *root* — the registrar's scan entry point."""
    base = Path(root)
    if not base.is_dir():
        return
    for path in sorted(base.rglob(filename)):
        yield BuildLedger(path)
