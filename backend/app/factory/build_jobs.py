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
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("cerebrumdev.factory.build_jobs")

BUILD_ENGINE_ENV = "FACTORY_BUILD_ENGINE"
RUNNER = "runner"
TEMPLATE = "template"

#: Wall-clock ceiling for a production Floor build. Code-only stays a
#: 20–30 min coder pass. When the factory LLM is configured the run
#: auto-continues into a Store-green (pilot) cycle and uses the 2-hour
#: combined budget — that is the production-grade path, not a refuse.
BUILD_WALL_CLOCK_ENV = "FACTORY_BUILD_WALL_CLOCK_S"
BUILD_REWORK_ENV = "FACTORY_BUILD_MAX_REWORK"
BUILD_PHASE_WALL_ENV = "FACTORY_PHASE_WALL_CLOCK_S"

#: Code-only Floor run: one WRITER pass (~25 min phase cap) inside 30 min.
#: Auto-pilot / explicit pilot: 2 hours and 3 WRITER reworks.
_DEFAULT_WALL_CLOCK_S = 1800.0
_DEFAULT_MAX_REWORK = 1
_DEFAULT_PHASE_WALL_CLOCK_S = 1500.0

#: A build with no ledger event for this long has no process behind it. The
#: longest legitimate gap is one coder call (up to ~3 min on a reasoning
#: model, x2 legs x retries), so this is set well above that.
_STALL_AFTER_S = 1800.0

#: Quieter than a dead process: one coder call can sit in the model for
#: ~2–3 min with no NOTE. Past this the UI says "quiet" so a customer can
#: tell a long model call from a frozen 2/5 with no name.
_STALE_AFTER_S = 180.0


def build_engine() -> str:
    """Which engine production builds with. Runner unless told otherwise."""
    raw = os.getenv(BUILD_ENGINE_ENV, RUNNER).strip().lower()
    return TEMPLATE if raw in {TEMPLATE, "legacy", "generator"} else RUNNER


def _uses_pilot_budget(cycle: str = "code", auto_pilot: bool = False) -> bool:
    return auto_pilot or (cycle or "code").strip().lower() == "pilot"


def _wall_clock_s(cycle: str = "code", auto_pilot: bool = False) -> float:
    raw = os.getenv(BUILD_WALL_CLOCK_ENV)
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass
    if _uses_pilot_budget(cycle, auto_pilot):
        from app.factory.build.auto_pilot import AUTO_PILOT_WALL_CLOCK_S

        return AUTO_PILOT_WALL_CLOCK_S
    return _DEFAULT_WALL_CLOCK_S


def _max_rework(cycle: str = "code", auto_pilot: bool = False) -> int:
    raw = os.getenv(BUILD_REWORK_ENV)
    if raw is not None:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    if _uses_pilot_budget(cycle, auto_pilot):
        from app.factory.build.auto_pilot import AUTO_PILOT_MAX_REWORK

        return AUTO_PILOT_MAX_REWORK
    return _DEFAULT_MAX_REWORK


def _phase_wall_clock_s() -> float:
    try:
        return float(
            os.getenv(BUILD_PHASE_WALL_ENV, str(int(_DEFAULT_PHASE_WALL_CLOCK_S)))
        )
    except ValueError:
        return _DEFAULT_PHASE_WALL_CLOCK_S


def _phase_ref(role: Any) -> Dict[str, str]:
    """Named phase for the Floor: id plus the job title, not just 2/5."""
    from app.factory.build.authority import BuildRole, role_contract

    resolved = role if hasattr(role, "value") else BuildRole(role)
    return {"id": resolved.value, "label": role_contract(resolved).title}


def _event_age_s(ts: str, fallback_s: float) -> float:
    if not ts:
        return fallback_s
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        import time

        return max(0.0, time.time() - parsed.timestamp())
    except ValueError:
        return fallback_s


# -- status ---------------------------------------------------------------


def _ledger_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / "build_ledger.jsonl"


def _cycle_fields(ledger: Any, terminal: Any) -> Dict[str, Any]:
    """Honest cycle label for the Floor: code SUCCESS is not pilot-ready."""
    payload = (getattr(terminal, "payload", None) or {}) if terminal else {}
    cycle = str(payload.get("cycle") or "").strip().lower()
    try:
        if ledger.pilot_ready():
            cycle = "pilot"
        elif ledger.pilot_cycle_open():
            cycle = "pilot"
        elif not cycle:
            cycle = "code"
    except Exception:  # noqa: BLE001
        cycle = cycle or "code"
    try:
        ready = bool(ledger.pilot_ready())
    except Exception:  # noqa: BLE001
        ready = bool(payload.get("pilot_ready"))
    return {"cycle": cycle or "code", "pilot_ready": ready}


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
        events = ledger.events()
        completed = {r.value for r in ledger.completed_roles()}
        terminal = ledger.terminal_event()
        interrupted = ledger.interrupted_role()
        resume = ledger.resume_point()
    except Exception as exc:  # noqa: BLE001 -- a torn ledger must not 500
        logger.warning("unreadable build ledger at %s: %s", path, exc)
        return {"state": "unknown", "detail": f"ledger unreadable: {exc}"}

    phases = [p.value for p in BUILD_PHASES]
    current_role = interrupted or resume
    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        current_role = BUILD_PHASES[-1]
    if current_role is not None:
        phase_index = phases.index(current_role.value) + 1
        nxt = (
            BUILD_PHASES[phase_index]
            if phase_index < len(BUILD_PHASES)
            else None
        )
    else:
        phase_index = min(len(phases), sum(1 for p in phases if p in completed) + 1)
        nxt = None

    last_any = events[-1] if events else None
    notes = [e for e in events if e.kind is EventKind.NOTE]
    last_note = notes[-1] if notes else None
    try:
        import time

        file_idle_s = time.time() - path.stat().st_mtime
    except OSError:
        file_idle_s = 0.0
    # Prefer the older of ledger ts vs file mtime. Tests (and a `touch`)
    # can age one without the other; a dead process leaves both stale.
    idle_s = max(_event_age_s(last_any.ts if last_any else "", file_idle_s), file_idle_s)

    monitor: Dict[str, Any] = {
        "current_phase": _phase_ref(current_role) if current_role else None,
        "phase_index": phase_index,
        "phase_total": len(phases),
        "next_phase": _phase_ref(nxt) if nxt else None,
        "last_event": (last_note or last_any).detail if (last_note or last_any) else None,
        "last_event_at": last_any.ts if last_any else None,
        "last_event_age_s": round(idle_s, 1),
        "stale": idle_s > _STALE_AFTER_S,
    }
    if last_note is not None:
        payload = last_note.payload or {}
        done, total = payload.get("done"), payload.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            monitor["phase_progress"] = {
                "done": done,
                "total": total,
                "fraction": round(done / total, 3),
                "stage": payload.get("stage"),
            }

    progress = {
        "phases": phases,
        "completed": [p for p in phases if p in completed],
        "phases_total": len(phases),
        "phases_done": sum(1 for p in phases if p in completed),
        **monitor,
        **_cycle_fields(ledger, terminal),
    }

    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        payload = terminal.payload or {}
        return {
            "state": "succeeded",
            "detail": terminal.detail,
            "cycle": payload.get("cycle") or "code",
            "outcome": payload.get("outcome"),
            # Only a SUCCESS that closed a pilot cycle is Store-green / pilot-ready.
            # Code-phase success must not be presented as a finished pilot.
            "pilot_ready": ledger.pilot_ready(),
            **progress,
            **_authorship(output_dir),
            "stale": False,
        }
    if terminal is not None and terminal.kind is EventKind.RUN_FAILED:
        payload = terminal.payload or {}
        return {
            "state": "failed",
            "detail": terminal.detail,
            "cycle": payload.get("cycle") or "code",
            "outcome": payload.get("outcome"),
            "pilot_ready": False,
            "findings": list(payload.get("findings") or [])[:10],
            **progress,
            "stale": False,
        }
    # Intra-phase activity. Without this a WRITER pass of ~16 agent calls
    # reports a frozen "2/5" for twenty minutes and a customer cannot tell
    # work from a hang.
    activity: Dict[str, Any] = {}
    if last_note is not None:
        activity = {
            "activity": last_note.detail,
            "activity_stage": (last_note.payload or {}).get("stage"),
            "activity_done": (last_note.payload or {}).get("done"),
            "activity_total": (last_note.payload or {}).get("total"),
        }

    # A build whose thread died (worker restart, OOM, redeploy) leaves the
    # ledger's last event as PHASE_STARTED forever, which read as "building"
    # for eternity. Age the file: no event for this long means nothing is
    # working on it, and saying so is the honest answer.
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


def _quota_marker(output_dir: Path) -> Path:
    return Path(output_dir) / ".generation_quota_account"


def _write_quota_marker(output_dir: Path, account_id: Optional[str]) -> None:
    if not account_id:
        return
    _quota_marker(output_dir).write_text(account_id, encoding="utf-8")


def _clear_quota_marker(output_dir: Path) -> None:
    try:
        _quota_marker(output_dir).unlink()
    except FileNotFoundError:
        return


def _refund_generation_quota(output_dir: Path) -> None:
    path = _quota_marker(output_dir)
    if not path.is_file():
        return
    try:
        account_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if account_id:
        from app.core.trial_limits import refund

        refund(account_id, "generation")
    _clear_quota_marker(output_dir)


def clone_steward_canonical(source_dir: Path | str) -> Path:
    """Copy a finished Steward tree to factory_outputs/Cerebrum-Steward.

    A second runner used to start here and double LLM spend. Clone only.
    """
    from app.factory.paths import factory_outputs_root, is_safe_to_clean

    src = Path(source_dir)
    dest = factory_outputs_root() / "Cerebrum-Steward"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if not is_safe_to_clean(dest):
            raise RuntimeError(f"refusing to replace unsafe canonical path {dest}")
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def _maybe_clone_steward_canonical(blueprint: Any, output_dir: Path) -> None:
    if getattr(blueprint, "product_id", None) != "cerebrum-steward":
        return
    try:
        dest = clone_steward_canonical(output_dir)
        logger.info("cloned Steward canonical copy to %s", dest)
    except Exception:  # noqa: BLE001 — clone must not fail the build record
        logger.exception("Steward canonical clone failed from %s", output_dir)


def _run(
    blueprint: Any,
    output_dir: Path,
    blocks_root: Optional[Path],
    cycle: str = "code",
) -> None:
    from app.factory.build.auto_pilot import factory_auto_pilot_enabled
    from app.factory.build.runner import BuildBudget, RoleRunner

    auto = cycle == "code" and factory_auto_pilot_enabled()
    try:
        runner = RoleRunner(
            blueprint,
            output_dir,
            blocks_root=blocks_root,
            budget=BuildBudget(
                max_rework=_max_rework(cycle, auto_pilot=auto),
                wall_clock_s=_wall_clock_s(cycle, auto_pilot=auto),
                phase_wall_clock_s=_phase_wall_clock_s(),
            ),
            cycle=cycle,
            auto_pilot=auto if cycle == "code" else False,
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
        _refund_generation_quota(output_dir)
        return
    from app.factory.build.ledger import BuildLedger, EventKind

    terminal = BuildLedger(_ledger_path(output_dir)).terminal_event()
    failed = terminal is not None and terminal.kind is EventKind.RUN_FAILED
    if failed:
        _refund_generation_quota(output_dir)
        return
    _maybe_clone_steward_canonical(blueprint, output_dir)
    _clear_quota_marker(output_dir)


def start_runner_build(
    blueprint: Any,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
    cycle: Optional[str] = None,
    quota_account_id: Optional[str] = None,
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

    resolved = (cycle or os.getenv("FACTORY_BUILD_SUITE") or "code").strip().lower()
    if resolved not in {"code", "pilot"}:
        resolved = "code"

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    inputs_hash = blueprint_hash(blueprint)

    # Open the run record BEFORE the thread starts. Otherwise a client that
    # polls immediately reads "unknown" -- indistinguishable from "no build
    # was ever started" -- for however long the thread takes to reach its
    # first ledger write. The runner sees an existing ledger with a matching
    # inputs_hash and resumes into it rather than starting a second run.
    ledger = BuildLedger(_ledger_path(out))
    if ledger.exists():
        status = build_status(out)
        if status.get("state") == "building":
            logger.info("refusing second runner start; build already in progress at %s", out)
            return {
                "engine": RUNNER,
                "output_dir": str(out),
                "product_id": getattr(blueprint, "product_id", "unknown"),
                "inputs_hash": inputs_hash,
                "build": status,
                "cycle": resolved,
                "already_running": True,
            }

    if not ledger.exists():
        ledger.start_run(
            product_id=getattr(blueprint, "product_id", "unknown"),
            inputs_hash=inputs_hash,
        )

    _write_quota_marker(out, quota_account_id)

    thread = threading.Thread(
        target=_run,
        args=(blueprint, out, Path(blocks_root) if blocks_root else None, resolved),
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
        "cycle": resolved,
        "already_running": False,
    }
