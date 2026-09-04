"""The role runner — drives a gated, lane-restricted, resumable build.

This is what turns the three kernels from inert checks into a manufacturing
run. It owns the phase order, hands each role a workspace it cannot write
outside of, runs the phase's gate afterwards, and drives the WRITER<->TESTER
rework loop until a gate passes or the budget is spent.

Three properties are load-bearing and each is enforced here rather than
trusted:

* **Gates are looked up by phase**, never supplied by the role. A role cannot
  weaken, mock or skip the check that judges it.
* **A spent budget is a failure.** A run that ends without its gates green
  terminates ``FAILED`` with the reason in the ledger. There is no code path
  that reports success for an ungated build -- that is the same "plausible
  green" hazard the gates were written against, and it must not be
  reintroduced one layer up.
* **Resume keys on the blueprint, not the output tree.** ``ProductGenerator``
  reports an ``inputs_hash`` that is really ``hash_tree`` of the generated
  output, so it changes whenever an LLM writes a handler. Keying resume on it
  would refuse every resume of an unchanged blueprint. :func:`blueprint_hash`
  hashes the inputs instead, which is stable by construction.

The role runner is the production default (``FACTORY_BUILD_ENGINE=runner``).
``ProductGenerator`` remains the template emitter and the source of the
14-class surfaces RoleRunner now converges onto. ``FACTORY_BUILD_ENGINE=template``
is the documented revert.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from app.factory.build.authority import (
    BUILD_PHASES,
    SEALED_AFTER_CLONER,
    AuthorityError,
    BuildRole,
    assert_phase_order,
    authority_manifest,
)
from app.factory.build.gates import (
    FACTORY_SUITE_MARKER_EXPR,
    GateContext,
    GateResult,
    gate_for,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import (
    ROLE_IMPLEMENTATIONS,
    RoleContext,
    RoleError,
    RoleResult,
)
from app.factory.build.workspace import RoleWorkspace

RUNNER_FLAG_ENV = "FACTORY_RUNNER_ENABLED"
LEDGER_FILENAME = "build_ledger.jsonl"

#: Phases that participate in the rework loop. A failed TESTER gate sends the
#: WRITER back round with the findings as its work list.
REWORK_SOURCE = BuildRole.TESTER
REWORK_TARGET = BuildRole.WRITER


def runner_enabled() -> bool:
    """The runner is opt-in. The template path stays the default until cutover."""
    return os.getenv(RUNNER_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def blueprint_hash(blueprint: Any) -> str:
    """Stable hash of the build's *inputs*.

    Canonical JSON with sorted keys, so it does not move with dict ordering,
    and it never touches the generated tree -- an output hash cannot be a
    resume key because it is unknown until the build it is meant to authorise
    has already run.
    """
    from app.factory.blueprint import blueprint_to_dict

    payload = json.dumps(
        blueprint_to_dict(blueprint), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    #: Instrumented collect-all run: every phase executed, every gate
    #: finding recorded, no halt. NOT a success — ``BuildOutcome.ok``
    #: stays False and no package identity is sealed. The build is an
    #: instrument report (board P7: one run logging ALL gate findings).
    COLLECT_ALL_REPORT = "COLLECT_ALL_REPORT"
    FAILED_GATE = "FAILED_GATE"
    FAILED_BUDGET_SPENT = "FAILED_BUDGET_SPENT"
    FAILED_ROLE_ERROR = "FAILED_ROLE_ERROR"
    FAILED_AUTHORITY = "FAILED_AUTHORITY"


def _collect_all_enabled() -> bool:
    """FACTORY_GATE_COLLECT_ALL read live (tests / operators flip it
    without re-import). When truthy the run loop records every failed
    gate and RoleError as findings and keeps going instead of halting,
    so ONE run surfaces the complete list (board P7 collect-all). Lane
    violations (AuthorityError) stay terminal — that is a security
    boundary, not a build finding."""
    raw = os.getenv("FACTORY_GATE_COLLECT_ALL", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BuildBudget:
    """Bounds on a run. Exhausting a bound ends the build as a failure.

    ``wall_clock_s`` is the whole build (2 hours on Store-green).
    ``phase_wall_clock_s`` caps *each* role. The 25-minute default is the
    code-only gate; a keyed auto-pilot / explicit pilot pass uses 90
    minutes so WRITER can do real coding instead of aborting at ~510s
    into the first handler. ``0`` disables that bound.
    """

    max_rework: int = 3
    wall_clock_s: float = 7200.0
    phase_wall_clock_s: float = 1500.0

    def deadline_from(self, started: float) -> Optional[float]:
        return (started + self.wall_clock_s) if self.wall_clock_s > 0 else None


@dataclass
class BuildOutcome:
    outcome: Outcome
    detail: str = ""
    failed_phase: Optional[BuildRole] = None
    rework_used: int = 0
    completed: tuple = ()
    findings: Sequence[str] = ()
    ledger_path: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "ok": self.ok,
            "detail": self.detail,
            "failed_phase": self.failed_phase.value if self.failed_phase else None,
            "rework_used": self.rework_used,
            "completed": [p.value for p in self.completed],
            "findings": list(self.findings),
            "ledger": self.ledger_path,
        }


class RoleRunner:
    """Drives one build of *blueprint* into *workspace*."""

    def __init__(
        self,
        blueprint: Any,
        workspace: Path | str,
        *,
        plan: Any = None,
        blocks_root: Optional[Path | str] = None,
        store_root: Optional[Path | str] = None,
        ledger: Optional[BuildLedger] = None,
        budget: Optional[BuildBudget] = None,
        roles: Optional[Mapping[BuildRole, Callable[[RoleContext], RoleResult]]] = None,
        gate_timeout_s: Optional[float] = None,
        subprocess_runner: Optional[Callable[..., Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        cycle: str = "code",
        auto_pilot: bool = False,
    ) -> None:
        from app.factory.planner import CapabilityPlanner, assert_generatable

        self.blueprint = blueprint
        self.workspace = Path(workspace).resolve()
        self.blocks_root = Path(blocks_root) if blocks_root else None
        self.store_root = Path(store_root) if store_root else None
        self.plan = (
            assert_generatable(plan)
            if plan
            else CapabilityPlanner(self.blocks_root).plan(blueprint)
        )
        self.budget = budget or BuildBudget()
        # Roles are injectable so a test can drive a misbehaving role; gates
        # are NOT -- see the module docstring.
        self.roles = dict(roles or ROLE_IMPLEMENTATIONS)
        self.gate_timeout_s = gate_timeout_s
        self.subprocess_runner = subprocess_runner
        self.clock = clock
        self.ledger = ledger or BuildLedger(self.workspace / LEDGER_FILENAME)
        self.state: Dict[str, Any] = {}
        self.manifest = authority_manifest()
        #: Set by run(); roles read it to stop starting coder calls
        #: that cannot finish inside the build's wall clock.
        self._deadline: Optional[float] = None
        resolved = (cycle or "code").strip().lower()
        self.cycle = "pilot" if resolved == "pilot" else "code"
        #: Floor ``_run`` passes True when a factory coder key is set.
        #: Direct RoleRunner callers (tests, CLI helpers) stay code-only
        #: unless they opt in — a keyed CI stub must not open Store-green.
        self.auto_pilot = bool(auto_pilot)

    # -- gate plumbing ---------------------------------------------------

    def _gate_context(self, role: BuildRole) -> GateContext:
        kwargs: Dict[str, Any] = {
            "workspace": self.workspace,
            "role": role,
            "gaps": tuple(self.state.get("gaps", ())),
            "vendored_blocks": tuple(self.state.get("vendored_blocks", ())),
        }
        if self.gate_timeout_s is not None:
            kwargs["timeout_s"] = self.gate_timeout_s
        if self.subprocess_runner is not None:
            kwargs["runner"] = self.subprocess_runner
        kwargs["suite_marker"] = (
            "pilot" if self.state.get("build_cycle") == "pilot" else FACTORY_SUITE_MARKER_EXPR
        )
        kwargs["cycle"] = str(self.state.get("build_cycle") or self.cycle or "code")
        kwargs["store_ops"] = tuple(self.state.get("store_ops") or ())
        kwargs["store_unwired"] = bool(self.state.get("store_unwired"))
        return GateContext(**kwargs)

    def _absorb(self, result: RoleResult) -> None:
        if result.gaps:
            self.state["gaps"] = tuple(result.gaps)
        if result.vendored_blocks:
            self.state["vendored_blocks"] = tuple(result.vendored_blocks)
        for key, value in (result.notes or {}).items():
            self.state[key] = value

    def _restore_workspace_state(self) -> None:
        """Rehydrate CLONER notes after a worker restart / pilot reopen.

        The runner's in-memory state dies with the process. A resume that
        skips CLONER would otherwise hand TESTER an empty vendored_blocks
        list and rewrite the suite against nothing.
        """
        vendor = self.workspace / "vendor" / "blocks"
        if vendor.is_dir():
            blocks = tuple(
                sorted(
                    p.name
                    for p in vendor.iterdir()
                    if (p / "block.py").is_file()
                )
            )
            if blocks:
                self.state.setdefault("vendored_blocks", blocks)
        lock_path = self.workspace / "blocks.lock.json"
        if lock_path.is_file() and "lock" not in self.state:
            try:
                self.state["lock"] = json.loads(
                    lock_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass

    # -- one phase -------------------------------------------------------

    def _run_phase(self, role: BuildRole, work_list: Sequence[str]) -> GateResult:
        """Run the role then its gate. Raises RoleError / AuthorityError up."""
        self.ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        # The WRITER is staged: it rewrites app/ wholesale on every rework
        # round, and the agent picks different entity names each call, so a
        # pass killed part-way through would leave models.py from one attempt
        # beside routes.py from another. Observed live as
        # "no such table: field_defect". Other roles append rather than
        # replace, so a partial pass is recoverable by re-running them.
        staging = (
            self.workspace.parent / f".{self.workspace.name}.staging-{role.value.lower()}"
            if role is BuildRole.WRITER
            else None
        )
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        sealed = (
            SEALED_AFTER_CLONER
            if role is not BuildRole.CLONER
            and BuildRole.CLONER in self.ledger.completed_roles()
            else ()
        )
        ws = RoleWorkspace(
            role,
            self.workspace,
            store_root=self.store_root,
            staging=staging,
            sealed=sealed,
        )
        def _progress(detail: str, payload: Dict[str, Any]) -> None:
            """Record intra-phase progress as a ledger NOTE.

            NOTE deliberately: it is not a verdict, so completed_roles(),
            resume_point() and the terminal-event readers are untouched -- a
            progress line can never be mistaken for a gate result.
            """
            self.ledger.append(EventKind.NOTE, role=role, detail=detail, payload=payload)

        deadline = self._deadline
        phase_cap = self.budget.phase_wall_clock_s
        if phase_cap and phase_cap > 0:
            phase_deadline = self.clock() + float(phase_cap)
            deadline = (
                phase_deadline
                if deadline is None
                else min(deadline, phase_deadline)
            )
        ctx = RoleContext(
            role=role,
            workspace=ws,
            blueprint=self.blueprint,
            plan=self.plan,
            blocks_root=self.blocks_root,
            work_list=tuple(work_list),
            state=self.state,
            progress=_progress,
            deadline=deadline,
        )
        result = self.roles[role](ctx)
        if not result.ok:
            raise RoleError(result.detail or f"{role.value} reported failure")
        # Only now does the staged pass become visible. Everything before this
        # line could be interrupted without the destination ever changing.
        ws.commit()
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        self._absorb(result)

        if role is BuildRole.CLONER:
            for bid in result.vendored_blocks:
                lock = (self.state.get("lock") or {}).get("blocks", {}).get(bid, {})
                self.ledger.record_clone(
                    block_id=bid,
                    source_commit=str(lock.get("commit", "unpinned")),
                    store_repo=str(lock.get("source", "unknown")),
                    vendored_path=str(lock.get("path", f"vendor/blocks/{bid}")),
                )

        gate = gate_for(role)
        verdict = gate(self._gate_context(role))
        kind = EventKind.GATE_PASSED if verdict.ok else EventKind.GATE_FAILED
        self.ledger.append(
            kind,
            role=role,
            detail=verdict.detail,
            payload={
                "gate": verdict.gate,
                "findings": list(verdict.findings),
                "role_detail": result.detail,
                "wrote": list(ws.written),
            },
        )
        return verdict

    # -- terminal bookkeeping --------------------------------------------

    def _finish(
        self,
        outcome: Outcome,
        detail: str,
        *,
        phase: Optional[BuildRole] = None,
        rework: int = 0,
        findings: Sequence[str] = (),
    ) -> BuildOutcome:
        kind = (
            EventKind.RUN_SUCCEEDED
            if outcome is Outcome.SUCCESS
            else EventKind.RUN_FAILED
        )
        if outcome is Outcome.SUCCESS:
            from app.factory.build.package import write_identity

            sealed = (
                SEALED_AFTER_CLONER
                if BuildRole.CLONER in self.ledger.completed_roles()
                else ()
            )
            ws = RoleWorkspace(
                BuildRole.WRITER,
                self.workspace,
                store_root=self.store_root,
                sealed=sealed,
            )
            write_identity(ws, extra={"engine": "role_runner"})
        self.ledger.append(
            kind,
            role=phase,
            detail=detail,
            payload={
                "outcome": outcome.value,
                "rework_used": rework,
                "findings": list(findings),
                "cycle": getattr(self, "cycle", "code"),
                "pilot_ready": getattr(self, "cycle", "code") == "pilot"
                and outcome is Outcome.SUCCESS,
            },
        )
        return BuildOutcome(
            outcome=outcome,
            detail=detail,
            failed_phase=phase,
            rework_used=rework,
            completed=tuple(p for p in BUILD_PHASES if p in self.ledger.completed_roles()),
            findings=list(findings),
            ledger_path=str(self.ledger.path),
        )

    def _should_auto_open_pilot(self) -> bool:
        """True when code-phase 5/5 must continue into a Store-green cycle."""
        return self.cycle != "pilot" and bool(self.auto_pilot)

    def _grant_pilot_budget(self) -> None:
        """Give the auto-opened pilot cycle its own wall and rework room.

        Floor ``_run`` used to pass a 30 min / 1-rework budget sized for the
        code phase alone. Continuing into pilot on that leftover (~15 min
        after a thin WRITER) is how a Store-green run dies as SUCCESS.
        """
        from app.factory.build.auto_pilot import (
            AUTO_PILOT_MAX_REWORK,
            PILOT_MIN_REMAINING_S,
        )

        remaining = None
        if self._deadline is not None:
            remaining = self._deadline - self.clock()
        if remaining is None or remaining < PILOT_MIN_REMAINING_S:
            add = PILOT_MIN_REMAINING_S - (remaining or 0.0)
            if self._deadline is None:
                self._deadline = self.clock() + PILOT_MIN_REMAINING_S
            else:
                self._deadline += add
            new_wall = (self.budget.wall_clock_s or 0.0) + add
        else:
            new_wall = self.budget.wall_clock_s
        self.budget = BuildBudget(
            max_rework=max(int(self.budget.max_rework), AUTO_PILOT_MAX_REWORK),
            wall_clock_s=new_wall,
            phase_wall_clock_s=self.budget.phase_wall_clock_s,
        )

    def _open_auto_pilot(self) -> None:
        """Reopen TESTER + STORE_MANAGER without writing a code SUCCESS."""
        self.ledger.append(
            EventKind.NOTE,
            detail=(
                "code-phase SUCCESS; auto-opening Store-green cycle "
                "(factory LLM configured)"
            ),
            payload={"cycle": "pilot", "auto_pilot": True},
        )
        self.ledger.open_pilot_cycle(
            reason="code-phase SUCCESS; auto-opening Store-green cycle"
        )
        self.cycle = "pilot"
        self.state["build_cycle"] = "pilot"
        self._grant_pilot_budget()

    # -- the run ---------------------------------------------------------

    def run(self) -> BuildOutcome:
        started = self.clock()
        deadline = self.budget.deadline_from(started)
        self._deadline = deadline
        inputs_hash = blueprint_hash(self.blueprint)

        # Refuse to continue a run whose blueprint changed underneath it.
        self.ledger.assert_resumable(inputs_hash=inputs_hash)
        if not self.ledger.exists():
            self.workspace.mkdir(parents=True, exist_ok=True)
            self.ledger.start_run(
                product_id=getattr(self.blueprint, "product_id", "unknown"),
                inputs_hash=inputs_hash,
            )

        from app.factory.build.preflight import evaluate_preflight

        preflight = evaluate_preflight()
        self.state["preflight"] = {
            "verdict": preflight["verdict"],
            "git_sha": preflight["git_sha"],
            "emitter": preflight["emitter_identity"]["id"],
            "kernel_ownership_ok": preflight["kernel_ownership"]["ok"],
        }
        if not preflight["ok"]:
            return self._finish(
                Outcome.FAILED_GATE,
                f"S0 preflight failed: {preflight.get('first_failing_criterion')}",
            )

        self._restore_workspace_state()
        if self.cycle == "pilot":
            self.state["build_cycle"] = "pilot"
            if (
                self.ledger.exists()
                and self.ledger.code_phase_succeeded()
                and not self.ledger.pilot_ready()
                and not self.ledger.pilot_cycle_open()
            ):
                self.ledger.open_pilot_cycle()

        done = self.ledger.completed_roles()
        rework_used = 0
        work_list: Sequence[str] = ()
        collected: list[str] = []

        index = 0
        while True:
            while index < len(BUILD_PHASES):
                role = BUILD_PHASES[index]
                if role in done:
                    index += 1
                    continue

                if deadline is not None and self.clock() >= deadline:
                    return self._finish(
                        Outcome.FAILED_BUDGET_SPENT,
                        f"wall-clock budget of {self.budget.wall_clock_s:g}s spent "
                        f"before {role.value} completed",
                        phase=role,
                        rework=rework_used,
                    )

                try:
                    assert_phase_order(role, done)
                    verdict = self._run_phase(role, work_list)
                except AuthorityError as exc:
                    self.ledger.append(
                        EventKind.PHASE_ABORTED,
                        role=role,
                        detail=f"lane violation: {exc}",
                    )
                    return self._finish(
                        Outcome.FAILED_AUTHORITY,
                        f"{role.value} wrote outside its lane: {exc}",
                        phase=role,
                        rework=rework_used,
                    )
                except RoleError as exc:
                    if _collect_all_enabled():
                        finding = f"{role.value}: role error: {exc}"
                        collected.append(finding)
                        self.ledger.append(
                            EventKind.NOTE,
                            role=role,
                            detail=f"COLLECT-ALL: halt suppressed — {finding}",
                            payload={"collect_all": True, "finding": finding},
                        )
                        done.add(role)
                        work_list = ()
                        index += 1
                        continue
                    self.ledger.append(
                        EventKind.PHASE_ABORTED, role=role, detail=str(exc)
                    )
                    return self._finish(
                        Outcome.FAILED_ROLE_ERROR,
                        f"{role.value} failed: {exc}",
                        phase=role,
                        rework=rework_used,
                    )

                if verdict.ok:
                    done.add(role)
                    work_list = ()
                    index += 1
                    continue

                # Gate failed. In collect-all mode every failed gate is a
                # recorded finding, never a halt: the phase is marked done so
                # later phases still run and ONE instrumented run surfaces the
                # complete list. No rework rounds either — a single linear pass.
                if _collect_all_enabled():
                    header = (
                        f"{role.value} gate '{verdict.gate}' failed: {verdict.detail}"
                    )
                    collected.append(header)
                    collected.extend(f"{role.value}: {f}" for f in verdict.findings)
                    self.ledger.append(
                        EventKind.NOTE,
                        role=role,
                        detail=f"COLLECT-ALL: halt suppressed — {header}",
                        payload={
                            "collect_all": True,
                            "gate": verdict.gate,
                            "findings": list(verdict.findings),
                        },
                    )
                    done.add(role)
                    work_list = ()
                    index += 1
                    continue

                # Only the TESTER sends work back to the WRITER;
                # every other failed gate is terminal, because there is no role
                # positioned to act on its findings.
                if role is not REWORK_SOURCE:
                    return self._finish(
                        Outcome.FAILED_GATE,
                        f"{role.value} gate '{verdict.gate}' failed: {verdict.detail}",
                        phase=role,
                        rework=rework_used,
                        findings=verdict.findings,
                    )

                if rework_used >= self.budget.max_rework:
                    return self._finish(
                        Outcome.FAILED_BUDGET_SPENT,
                        f"rework budget of {self.budget.max_rework} exhausted; "
                        f"{REWORK_SOURCE.value} gate still failing: {verdict.detail}",
                        phase=role,
                        rework=rework_used,
                        findings=verdict.findings,
                    )

                rework_used += 1
                work_list = tuple(verdict.findings)
                self.ledger.append(
                    EventKind.REWORK,
                    role=REWORK_TARGET,
                    detail=f"round {rework_used}: {verdict.detail}",
                    payload={"findings": list(verdict.findings)},
                )
                # Send the WRITER back round. Its earlier pass no longer counts.
                done.discard(REWORK_TARGET)
                index = BUILD_PHASES.index(REWORK_TARGET)

            if collected:
                return self._finish(
                    Outcome.COLLECT_ALL_REPORT,
                    f"collect-all: all phases ran; {len(collected)} finding(s) "
                    "recorded — instrument report, not a clean pass",
                    rework=rework_used,
                    findings=collected,
                )

            # Code-phase 5/5 is not Store-green. When a factory coder key is
            # configured, open a pilot cycle on the same workspace instead of
            # writing SUCCESS and parking the Floor on a thin prototype.
            if self.cycle != "pilot" and self._should_auto_open_pilot():
                self._open_auto_pilot()
                deadline = self._deadline
                rework_used = 0
                work_list = ()
                done = self.ledger.completed_roles()
                index = BUILD_PHASES.index(BuildRole.TESTER)
                continue
            break

        # THE VERDICT LINE (owner's ruling 1, 2026-09-01).
        #
        # "all phase gates passed" was a claim nobody could check. The
        # code-phase suite runs ``pytest -m "not pilot"`` and
        # ``@pytest.mark.pilot`` is the marker on the only tests that
        # exercise a business action -- so the sentence meant "everything
        # except the tests that check the product works passed", and
        # residential-lettings shipped a booting 216-file zip that could not
        # persist one record while every gate was green.
        #
        # Three gates now, each named WITH ITS SCOPE, and a gate that did not
        # run says so rather than being folded into a pass.
        # The gates themselves ran as phases; this sentence only has to stop
        # overclaiming what they covered. PRODUCT is the pilot cycle's TESTER
        # gate (see gates.gate_tester_contract), so reaching here on the
        # pilot cycle means it passed -- and reaching here on the code cycle
        # means it never ran, which must be said rather than implied.
        from app.factory.build.product_gate import GATE_SCOPES

        code_line = "CODE PASS — %s" % GATE_SCOPES["CODE"]
        if self.cycle != "pilot":
            return self._finish(
                Outcome.SUCCESS,
                "; ".join((
                    code_line,
                    "PRODUCT NOT RUN — %s" % GATE_SCOPES["PRODUCT"],
                    "STORE NOT RUN — %s" % GATE_SCOPES["STORE"],
                )),
                rework=rework_used,
            )
        return self._finish(
            Outcome.SUCCESS,
            "; ".join((
                code_line,
                "PRODUCT PASS — %s" % GATE_SCOPES["PRODUCT"],
                "STORE PASS — %s" % GATE_SCOPES["STORE"],
            )),
            rework=rework_used,
        )
