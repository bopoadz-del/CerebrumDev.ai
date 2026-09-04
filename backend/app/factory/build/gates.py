"""Phase gates — the checks a role must pass before the next role starts.

A gate is the only reason a long build converges instead of drifting. Each
role hands over a workspace and a claim about it; the gate is what decides
whether the claim is true, independently of anything the role said about its
own work.

Gates are values, not methods on the roles, for exactly that reason -- a
role cannot supply, weaken or skip the check that judges it. The runner
looks the gate up by phase and runs it against the workspace.

Every gate returns a :class:`GateResult` rather than raising, because a
failure is normal control flow here: it is what sends the WRITER back round
for another pass. Only a gate that cannot run at all raises.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

from app.factory.build.authority import BuildRole
from app.factory.build.pilot_durability import gate_pilot_outcome_survives_restart
from app.factory.build.ui_surface import gate_ui_surface
from app.factory.build.vendored_integrity import gate_vendored_integrity
from app.factory.build.writer_behaviour import gate_writer_behaviour

#: Wall-clock ceiling for a single gate subprocess. A gate that hangs would
#: silently consume the whole build budget.
DEFAULT_GATE_TIMEOUT_S = 600.0

#: Factory TESTER runs the code-phase suite only. Store-backed execute-all
#: lives on ``@pytest.mark.pilot`` and is not this gate: a complete platform
#: as designed is a later phase, not a 20–30 minute coder pass.
FACTORY_SUITE_MARKER_EXPR = "not pilot"


@dataclass(frozen=True)
class GateResult:
    ok: bool
    gate: str
    detail: str = ""
    #: Machine-readable specifics the runner records in the ledger and the
    #: next WRITER pass reads as its work list.
    findings: List[str] = field(default_factory=list)
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "gate": self.gate,
            "detail": self.detail,
            "findings": list(self.findings),
            "payload": dict(self.payload),
        }


class Gate(Protocol):
    """A check over a finished phase."""

    name: str

    def __call__(self, ctx: "GateContext") -> GateResult: ...


@dataclass(frozen=True)
class GateContext:
    """What a gate is allowed to look at."""

    workspace: Path
    role: BuildRole
    #: Populated by the COLLECTOR: capabilities with no adequate block.
    gaps: tuple = ()
    #: Populated by the CLONER: block ids vendored into the workspace.
    vendored_blocks: tuple = ()
    timeout_s: float = DEFAULT_GATE_TIMEOUT_S
    #: Injected so tests drive the gates without spawning real subprocesses.
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
    #: pytest ``-m`` expression. Code-phase is ``not pilot``; the Store-green
    #: cycle is ``pilot``.
    suite_marker: str = FACTORY_SUITE_MARKER_EXPR
    #: ``code`` (factory 5/5) or ``pilot`` (Store-green).
    cycle: str = "code"
    #: STORE_MANAGER decisions recorded for this cycle.
    store_ops: tuple = ()
    #: True when CEREBRUM_API_URL is unset — local clone-register reads only.
    store_unwired: bool = False

    def run(self, argv: List[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        run = self.runner or _real_run
        return run(argv, cwd=cwd or self.workspace, timeout=self.timeout_s)


def _real_run(argv: List[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    import os

    # UTF-8, unconditionally. On Windows a gate subprocess otherwise inherits
    # the console codepage, and a vendored block that prints one checkmark
    # ("✓") dies with a charmap UnicodeEncodeError that looks like a
    # block failure -- measured live on the team block.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        # A gate must never inherit an interactive stdin -- a subprocess that
        # blocks on input would stall the build with no diagnosis.
        stdin=subprocess.DEVNULL,
    )


# -- suite failure classes (Floor banner is verdict.detail only) ---------

#: Needles the PRODUCT / TESTER banner must name. Live VetCare Hub
#: sess_5dfb4a3 showed only "suite is red" while pytest had a concrete
#: class — rework then regenerated every handler because findings named
#: no capability.
_SUITE_ASSERTION_CLASSES: tuple[tuple[str, str], ...] = (
    ("KeyError: 'items'", "accept-payload list shape (KeyError: 'items')"),
    ('KeyError: "items"', "accept-payload list shape (KeyError: 'items')"),
    (
        "rejected a payload built from its own schema",
        "schema sample refused",
    ),
    (
        "status must be one of",
        "schema sample refused (status vocabulary)",
    ),
    (
        "Unknown channel:",
        "schema sample refused (notification channel)",
    ),
    (
        "not JSON serializable",
        "e2e handle() result not JSON serializable",
    ),
    (
        "Object of type bytes",
        "e2e handle() result not JSON serializable",
    ),
    (
        "accepted a record but persisted nothing",
        "accept-payload persisted nothing",
    ),
    ("create_persists", "domain acceptance: create_persists"),
    ("list_only_persisted", "domain acceptance: list_only_persisted"),
    ("queue_item_processed", "domain acceptance: queue_item_processed"),
    ("Missing required field", "Missing required field"),
    ("no such table", "no such table"),
    ("No module named", "missing module"),
)


def suite_assertion_classes(findings: List[str], raw: str = "") -> List[str]:
    """Stable labels for the pytest lines a red PRODUCT suite produced."""
    blob = "\n".join(findings) + "\n" + (raw or "")
    seen: List[str] = []
    for needle, label in _SUITE_ASSERTION_CLASSES:
        if needle in blob and label not in seen:
            seen.append(label)
    return seen


def classify_suite_red(findings: List[str], raw: str = "") -> str:
    """PRODUCT / Floor detail must name the assertion class, not only 'red'."""
    classes = suite_assertion_classes(findings, raw)
    failed = next((ln.strip() for ln in findings if ln.startswith("FAILED")), "")
    err = next((ln.strip() for ln in findings if ln.startswith("E ")), "")
    snippet = " ".join(part for part in (failed, err) if part) or next(
        (ln.strip() for ln in findings if ln.strip()), ""
    )
    if snippet and len(snippet) > 240:
        snippet = snippet[:237] + "..."
    if classes and snippet:
        return "suite is red: " + "; ".join(classes[:3]) + " — " + snippet
    if classes:
        return "suite is red: " + "; ".join(classes[:3])
    if snippet:
        return "suite is red: " + snippet
    return "suite is red"


# -- individual gates ----------------------------------------------------


def gate_gaps_enumerated(ctx: GateContext) -> GateResult:
    """COLLECTOR: every capability is either backed or declared a gap.

    The failure this exists for is the silent one -- a collector that plans
    around a missing block by dropping the capability, so the platform ships
    without it and nothing in the artifact says so.
    """
    unresolved = [g for g in ctx.gaps if not str(g).strip()]
    if unresolved:
        return GateResult(
            ok=False,
            gate="gaps_enumerated",
            detail="collector reported an unnamed gap",
            findings=[f"gap {i} has no capability id" for i, _ in enumerate(unresolved)],
        )
    return GateResult(
        ok=True,
        gate="gaps_enumerated",
        detail=f"{len(ctx.gaps)} gap(s) declared for the writer",
        payload={"gaps": list(ctx.gaps)},
    )


def gate_blocks_import_offline(ctx: GateContext) -> GateResult:
    """CLONER: every vendored block imports with no store configured.

    This is the gate that makes a delivered platform standalone. It runs
    with CEREBRUM_API_URL deliberately absent, so a block that still reaches
    for the store at import time fails here rather than in the customer's
    environment.
    """
    vendor = ctx.workspace / "vendor" / "blocks"
    if not vendor.is_dir():
        return GateResult(
            ok=False,
            gate="blocks_import_offline",
            detail="vendor/blocks is missing — nothing was cloned",
            findings=["cloner produced no vendored blocks"],
        )

    missing = [b for b in ctx.vendored_blocks if not (vendor / b / "block.py").is_file()]
    if missing:
        return GateResult(
            ok=False,
            gate="blocks_import_offline",
            detail=f"{len(missing)} block(s) registered but not on disk",
            findings=[f"vendor/blocks/{b}/block.py missing" for b in missing],
        )

    proc = ctx.run([sys.executable, "-c", _IMPORT_PROBE])
    if proc.returncode != 0:
        return GateResult(
            ok=False,
            gate="blocks_import_offline",
            detail="a vendored block failed to import offline",
            findings=[ln for ln in (proc.stderr or "").splitlines() if ln.strip()][-10:],
        )
    return GateResult(
        ok=True,
        gate="blocks_import_offline",
        detail=f"{len(ctx.vendored_blocks)} block(s) import with no store configured",
    )


#: Imports every vendored block by file path, with the store env stripped.
_IMPORT_PROBE = """
import importlib.util, os, pathlib, sys
for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
    os.environ.pop(var, None)
failed = []
for mod in sorted(pathlib.Path("vendor/blocks").glob("*/block.py")):
    name = "vendored_" + mod.parent.name
    spec = importlib.util.spec_from_file_location(name, mod)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        failed.append(mod.parent.name + ": " + type(exc).__name__ + ": " + str(exc))
if failed:
    sys.stderr.write("\\n".join(failed))
    raise SystemExit(1)
"""


def gate_workspace_compiles(ctx: GateContext) -> GateResult:
    """WRITER: everything under app/ is at least syntactically real.

    Cheap and non-negotiable. A writer pass that emits a file which cannot
    be parsed must not reach the tester, where the failure would surface as
    a confusing collection error instead of a compile error.
    """
    app_dir = ctx.workspace / "app"
    if not app_dir.is_dir():
        return GateResult(
            ok=False,
            gate="workspace_compiles",
            detail="app/ is missing — the writer produced nothing",
            findings=["no app/ directory"],
        )

    proc = ctx.run([sys.executable, "-m", "compileall", "-q", "app"])
    if proc.returncode != 0:
        output = ((proc.stdout or "") + (proc.stderr or "")).splitlines()
        return GateResult(
            ok=False,
            gate="workspace_compiles",
            detail="app/ does not compile",
            findings=[ln for ln in output if ln.strip()][-20:],
        )
    return GateResult(ok=True, gate="workspace_compiles", detail="app/ compiles")


def gate_suite_green(ctx: GateContext) -> GateResult:
    """TESTER: the code-phase suite runs and passes.

    This gate judges the coder's 20–30 minute pass: imports, dispatch load,
    model round-trip, routes that answer HTTP 200 JSON, handle() returning a
    mapping. Store-backed ``ok: True`` / nested-error scans are
    ``@pytest.mark.pilot`` and are *not* this gate.

    An empty or missing suite fails. "No tests ran" is the single most
    dangerous green in a generated platform -- it looks identical to success
    in every summary line.
    """
    tests_dir = ctx.workspace / "tests"
    if not tests_dir.is_dir() or not any(tests_dir.rglob("test_*.py")):
        return GateResult(
            ok=False,
            gate="suite_green",
            detail="no tests were written",
            findings=["tester produced no test files"],
        )

    marker = (ctx.suite_marker or FACTORY_SUITE_MARKER_EXPR).strip() or FACTORY_SUITE_MARKER_EXPR
    gate_name = "pilot_green" if marker == "pilot" else "suite_green"
    proc = ctx.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "--no-header",
            "-m",
            marker,
        ]
    )
    raw = (proc.stdout or "") + (proc.stderr or "")
    output = raw.splitlines()
    if proc.returncode != 0:
        findings = [
            ln for ln in output if ln.startswith(("FAILED", "ERROR", "E "))
        ][:20]
        # "The suite could not run" is NOT "the suite failed". Production
        # builds failed three rework rounds with detail "suite is red" and
        # ZERO findings because the image had no pytest: a missing test
        # runner masquerading as bad generated code, which sent the agent
        # back to rewrite working handlers. Name the real cause instead.
        cannot_run = (
            "No module named pytest" in raw
            or "No module named 'pytest'" in raw
            or (not findings and "error" in raw.lower() and "collected" not in raw)
        )
        if cannot_run:
            return GateResult(
                ok=False,
                gate=gate_name,
                detail=(
                    "the suite could not be RUN (test runner unavailable or "
                    "collection failed) — this is a build-environment fault, "
                    "not a failing test"
                ),
                findings=[ln for ln in output if ln.strip()][-8:]
                or ["pytest produced no output"],
                payload={"returncode": proc.returncode, "infrastructure": True},
            )
        classified_findings = findings or [ln for ln in output if ln.strip()][-8:]
        return GateResult(
            ok=False,
            gate=gate_name,
            detail=classify_suite_red(classified_findings, raw),
            # Never report a failure with nothing to act on: fall back to the
            # output tail so a rework round has something concrete.
            findings=classified_findings,
            payload={
                "returncode": proc.returncode,
                "assertion_classes": suite_assertion_classes(
                    classified_findings, raw
                ),
            },
        )
    return GateResult(
        ok=True,
        gate=gate_name,
        detail=output[-1].strip() if output else "suite passed",
    )


def gate_tester_contract(ctx: GateContext) -> GateResult:
    """TESTER: the code-phase suite, or -- on the pilot cycle -- PRODUCT.

    OWNER'S RULING 1, 2026-09-01 (FINDING 3). The code-phase gate runs
    ``pytest -m "not pilot"``, and ``@pytest.mark.pilot`` is the marker on
    the only tests that exercise a business action. So "all phase gates
    passed" meant "everything except the tests that check the product works
    passed", and residential-lettings shipped a booting 216-file zip that
    could not persist one record with every gate green.

    The code-phase gate is UNCHANGED: a 20-30 minute coder pass is not where
    a product is judged. What changes is that the pilot cycle's TESTER phase
    is now the PRODUCT gate -- the pilot-marked suite against the booted
    product, plus a one-record round-trip per capability (R1e) -- rather
    than the same suite runner with a different marker.
    """
    from app.factory.build.product_gate import gate_product

    if (ctx.cycle or "code").strip().lower() == "pilot":
        return gate_product(ctx)
    return gate_suite_green(ctx)


def gate_store_ops_authorised(ctx: GateContext) -> GateResult:
    """STORE_MANAGER: nothing was published without passing its op gate.

    The authority model lives in app.factory.store_manager; this gate only
    asserts the runner recorded a decision for every op, so an unrecorded
    publish cannot pass as an authorised one.

    Code-phase 5/5 still accepts an empty register (historical: the role
    applied no op). The pilot cycle requires at least one authorised op
    (local ``STORE_READ`` of the clone register counts when the Store URL
    is unset).
    """
    if ctx.cycle != "pilot":
        return GateResult(
            ok=True,
            gate="store_ops_authorised",
            detail="no store ops applied",
        )
    if not ctx.store_ops:
        return GateResult(
            ok=False,
            gate="store_ops_authorised",
            detail="pilot cycle recorded no store ops",
            findings=["STORE_MANAGER applied no store op"],
        )
    detail = f"applied {len(ctx.store_ops)} store op(s)"
    if ctx.store_unwired:
        detail += "; store unwired (CEREBRUM_API_URL unset) — local clone-register reads only"
    return GateResult(
        ok=True,
        gate="store_ops_authorised",
        detail=detail,
        payload={
            "store_ops": list(ctx.store_ops),
            "store_unwired": ctx.store_unwired,
        },
    )


def gate_cloner_contract(ctx: GateContext) -> GateResult:
    """CLONER: blocks import offline *and* match their published digests.

    Import proves the clone runs; integrity proves it is the clone it claims
    to be. A vendored tree that imports cleanly but no longer matches the
    block's own manifest is a stale mirror or a tampered copy, and neither
    is visible from an import check.
    """
    imported = gate_blocks_import_offline(ctx)
    if not imported.ok:
        return imported
    integrity = gate_vendored_integrity(ctx)
    if not integrity.ok:
        return integrity
    return GateResult(
        ok=True,
        gate="cloner_contract",
        detail=f"{imported.detail}; {integrity.detail}",
        payload=dict(integrity.payload),
    )


def gate_writer_contract(ctx: GateContext) -> GateResult:
    """WRITER: the workspace parses *and* fails closed when a block fails.

    Compilation alone was the whole WRITER gate, so a route that discarded
    its handler's result and persisted anyway passed every phase and reached
    the customer. Syntax first because it is cheap and its failure mode is
    clearer; behaviour second because that is the claim worth checking.
    """
    compiled = gate_workspace_compiles(ctx)
    if not compiled.ok:
        return compiled
    behaviour = gate_writer_behaviour(ctx)
    if not behaviour.ok:
        return behaviour
    surface = gate_ui_surface(ctx)
    if not surface.ok:
        return surface
    return GateResult(
        ok=True,
        gate="writer_contract",
        detail=f"{compiled.detail}; {behaviour.detail}; {surface.detail}",
        findings=list(behaviour.findings),
        payload=dict(behaviour.payload),
    )


def gate_store_manager_contract(ctx: GateContext) -> GateResult:
    """STORE_MANAGER: store ops authorised, and on pilot, data that survives.

    This is the phase whose SUCCESS makes ``pilot_ready`` true, so it is the
    last place a durability claim can be checked before the flag is emitted.
    On the code cycle the durability gate is a no-op.
    """
    authorised = gate_store_ops_authorised(ctx)
    if not authorised.ok:
        return authorised
    durable = gate_pilot_outcome_survives_restart(ctx)
    if not durable.ok:
        return durable
    return GateResult(
        ok=True,
        gate="store_manager_contract",
        detail=f"{authorised.detail}; {durable.detail}",
        payload=dict(authorised.payload),
    )


GATES: Mapping[BuildRole, Gate] = {
    BuildRole.COLLECTOR: gate_gaps_enumerated,
    BuildRole.CLONER: gate_cloner_contract,
    BuildRole.WRITER: gate_writer_contract,
    BuildRole.TESTER: gate_tester_contract,
    BuildRole.STORE_MANAGER: gate_store_manager_contract,
}


def gate_for(role: BuildRole | str) -> Gate:
    return GATES[BuildRole(role)]
