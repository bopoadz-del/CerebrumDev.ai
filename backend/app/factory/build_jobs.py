"""Production product builds, run through the role runner.

This is the cutover seam. Until now every production door -- session
generate, the chat flow, /v1/factory/generate -- called
``ProductGenerator``, the deterministic template path: one handler template
per capability differing only in a ``BLOCK_IDS`` list, dispatching to the
operator's block store over HTTP. A downloaded product therefore could not
run without the store, and no capability logic was ever written for it.

The role runner (``app.factory.build``) is the manufacturing path: the
coding agent writes each handler, model spec and route against the block's
real contract, blocks are vendored WITH the Store runtime slice they stand
on, dispatch is in-process, and five gates judge the result. It is what
"the coding agent builds the platform" actually means.

The runner cannot run inside a request. A real build is minutes, not the
template's seconds, so it runs on a background thread and the **build
ledger is the job record** -- an append-only JSONL the runner already
fsyncs per event, which makes status a read of the artifact rather than
state held in this process. A process restart therefore loses no progress
report, and a resumed build reuses the same ledger.

Engine selection is an env switch (``FACTORY_BUILD_ENGINE``) with the
runner as the default, so a deployment can fall back to the template path
without a code change if a build regression appears in production.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("cerebrumdev.factory.build_jobs")

BUILD_ENGINE_ENV = "FACTORY_BUILD_ENGINE"
RUNNER = "runner"
TEMPLATE = "template"

#: Wall-clock ceiling for a production build. Past this the runner reports
#: FAILED_BUDGET_SPENT rather than holding a thread forever.
BUILD_WALL_CLOCK_ENV = "FACTORY_BUILD_WALL_CLOCK_S"
BUILD_REWORK_ENV = "FACTORY_BUILD_MAX_REWORK"

#: A build with no ledger event for this long has no process behind it. The
#: longest legitimate gap is one coder call (up to ~3 min on a reasoning
#: model, x2 legs x retries), so this is set well above that.
_STALL_AFTER_S = 1800.0


def build_engine() -> str:
    """Which engine production builds with. Runner unless told otherwise."""
    raw = os.getenv(BUILD_ENGINE_ENV, RUNNER).strip().lower()
    return TEMPLATE if raw in {TEMPLATE, "legacy", "generator"} else RUNNER


def _wall_clock_s() -> float:
    try:
        return float(os.getenv(BUILD_WALL_CLOCK_ENV, "2700"))
    except ValueError:
        return 2700.0


def _max_rework() -> int:
    try:
        return max(0, int(os.getenv(BUILD_REWORK_ENV, "3")))
    except ValueError:
        return 3


# -- status ---------------------------------------------------------------


def _ledger_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / "build_ledger.jsonl"


def _authorship(output_dir: Path | str) -> Dict[str, Any]:
    """Who wrote the finished artifact, and what the agent could not write.

    The template path disclosed this in the chat message at generation time
    ("N capability(ies) shipped as honest stubs — the coder could not write
    them"). A background build cannot: nothing is stubbed yet when it
    starts. Moving the disclosure to the completion status is what keeps it
    truthful -- degraded output is acceptable, invisible degradation is not.
    """
    import json

    manifest = Path(output_dir) / "docs" / "build_provenance.json"
    if not manifest.is_file():
        return {}
    try:
        prov = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sources = prov.get("artifact_sources") or {}
    agent = sorted(k for k, v in sources.items() if str(v).startswith("coder LLM"))
    failures = prov.get("coder_failures") or {}
    return {
        "authorship": {
            "artifacts": len(sources),
            "agent_written": len(agent),
            "templated": len(sources) - len(agent),
            "agent_artifacts": agent,
            # Named, not counted: "3 stubs" tells the customer nothing about
            # which parts of their platform are degraded.
            "coder_failures": {k: str(v)[:300] for k, v in failures.items()},
        }
    }


def build_status(output_dir: Path | str) -> Dict[str, Any]:
    """Read the build's state off disk.

    Deliberately reads the LEDGER rather than any in-process registry: the
    ledger is fsynced per event by the runner, so this answer survives a
    worker restart and is the same answer the shipped artifact carries.
    """
    from app.factory.build.authority import BUILD_PHASES
    from app.factory.build.ledger import BuildLedger, EventKind

    path = _ledger_path(output_dir)
    if not path.is_file():
        return {"state": "unknown", "detail": "no build ledger for this product"}

    ledger = BuildLedger(path)
    try:
        completed = {r.value for r in ledger.completed_roles()}
        terminal = ledger.terminal_event()
    except Exception as exc:  # noqa: BLE001 -- a torn ledger must not 500
        logger.warning("unreadable build ledger at %s: %s", path, exc)
        return {"state": "unknown", "detail": f"ledger unreadable: {exc}"}

    phases = [p.value for p in BUILD_PHASES]
    progress = {
        "phases": phases,
        "completed": [p for p in phases if p in completed],
        "phases_total": len(phases),
        "phases_done": sum(1 for p in phases if p in completed),
    }

    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        return {
            "state": "succeeded",
            "detail": terminal.detail,
            **progress,
            **_authorship(output_dir),
        }
    if terminal is not None and terminal.kind is EventKind.RUN_FAILED:
        return {
            "state": "failed",
            "detail": terminal.detail,
            "findings": list((terminal.payload or {}).get("findings") or [])[:10],
            **progress,
        }
    # Intra-phase activity. Without this a WRITER pass of ~16 agent calls
    # reports a frozen "2/5" for twenty minutes and a customer cannot tell
    # work from a hang.
    activity: Dict[str, Any] = {}
    try:
        notes = [e for e in ledger.events() if e.kind is EventKind.NOTE]
    except Exception:  # noqa: BLE001
        notes = []
    if notes:
        last = notes[-1]
        activity = {
            "activity": last.detail,
            "activity_stage": (last.payload or {}).get("stage"),
            "activity_done": (last.payload or {}).get("done"),
            "activity_total": (last.payload or {}).get("total"),
        }

    # A build whose thread died (worker restart, OOM, redeploy) leaves the
    # ledger's last event as PHASE_STARTED forever, which read as "building"
    # for eternity. Age the file: no event for this long means nothing is
    # working on it, and saying so is the honest answer.
    try:
        import time

        idle_s = time.time() - path.stat().st_mtime
    except OSError:
        idle_s = 0.0
    if idle_s > _STALL_AFTER_S:
        return {
            "state": "stalled",
            "detail": (
                f"no build activity for {int(idle_s // 60)} min — the build "
                "process is gone (restart or redeploy); generate again"
            ),
            **progress,
            **activity,
        }

    return {"state": "building", "detail": "build in progress", **progress, **activity}


def is_build_complete(output_dir: Path | str) -> bool:
    """True only for a finished, successful build.

    The download path gates on this. Zipping a workspace mid-build would
    hand the customer a splice of two writer passes -- the exact torn
    artifact the runner's staging protects against internally.
    """
    return build_status(output_dir).get("state") == "succeeded"


# -- starting a build -----------------------------------------------------


def _run(blueprint: Any, output_dir: Path, blocks_root: Optional[Path]) -> None:
    from app.factory.build.runner import BuildBudget, RoleRunner

    try:
        runner = RoleRunner(
            blueprint,
            output_dir,
            blocks_root=blocks_root,
            budget=BuildBudget(
                max_rework=_max_rework(), wall_clock_s=_wall_clock_s()
            ),
        )
        outcome = runner.run()
        logger.info(
            "runner build finished: product=%s outcome=%s rework=%s",
            getattr(blueprint, "product_id", "unknown"),
            outcome.outcome.value if hasattr(outcome.outcome, "value") else outcome.outcome,
            outcome.rework_used,
        )
    except Exception:  # noqa: BLE001
        # The thread must never die silently: without this the ledger's last
        # event stays PHASE_STARTED and status reads "building" forever.
        logger.exception("runner build crashed for %s", output_dir)
        try:
            from app.factory.build.ledger import BuildLedger, EventKind

            BuildLedger(_ledger_path(output_dir)).append(
                EventKind.RUN_FAILED, detail="build thread crashed; see service logs"
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not record the crash in the ledger")


def start_runner_build(
    blueprint: Any,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Start a background runner build and return immediately.

    Returns the same keys the template path returns (``output_dir``,
    ``product_id``, ``inputs_hash``) so callers and stored session state do
    not need a second shape, plus ``build`` carrying the live status. The
    hash is the BLUEPRINT hash: the output tree does not exist yet, and
    keying on it -- as ``ProductGenerator.inputs_hash`` does -- would make
    resume impossible.
    """
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.runner import blueprint_hash

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    inputs_hash = blueprint_hash(blueprint)

    # Open the run record BEFORE the thread starts. Otherwise a client that
    # polls immediately reads "unknown" -- indistinguishable from "no build
    # was ever started" -- for however long the thread takes to reach its
    # first ledger write. The runner sees an existing ledger with a matching
    # inputs_hash and resumes into it rather than starting a second run.
    ledger = BuildLedger(_ledger_path(out))
    if not ledger.exists():
        ledger.start_run(
            product_id=getattr(blueprint, "product_id", "unknown"),
            inputs_hash=inputs_hash,
        )

    thread = threading.Thread(
        target=_run,
        args=(blueprint, out, Path(blocks_root) if blocks_root else None),
        name=f"build-{getattr(blueprint, 'product_id', 'product')}",
        daemon=True,
    )
    thread.start()
    logger.info("runner build started for %s at %s", inputs_hash[:12], out)

    return {
        "engine": RUNNER,
        "output_dir": str(out),
        "product_id": getattr(blueprint, "product_id", "unknown"),
        "inputs_hash": inputs_hash,
        "build": build_status(out),
    }
