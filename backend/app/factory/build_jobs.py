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
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger("cerebrumdev.factory.build_jobs")

BUILD_ENGINE_ENV = "FACTORY_BUILD_ENGINE"
RUNNER = "runner"
TEMPLATE = "template"

#: Wall-clock ceiling for a production Floor build. Code-only stays a
#: 20–30 min coder pass. Keyed auto-pilot starts the same ~30 min stage,
#: hard-stops, inspects, and may bump to ~45 min. An explicit leftover
#: 2h env is honoured (never slashed) but is not the default path.
BUILD_WALL_CLOCK_ENV = "FACTORY_BUILD_WALL_CLOCK_S"
BUILD_REWORK_ENV = "FACTORY_BUILD_MAX_REWORK"
BUILD_PHASE_WALL_ENV = "FACTORY_PHASE_WALL_CLOCK_S"

#: Code-only Floor run: one WRITER pass (~25 min phase cap) inside 30 min.
#: Auto-pilot / explicit pilot: start 30 min, inspect, optional 45 min,
#: 3 WRITER reworks. Phase cap stays 90 min so a leftover high wall is
#: not clipped by the old 25 min abort.
_DEFAULT_WALL_CLOCK_S = 1800.0
_DEFAULT_MAX_REWORK = 1
_DEFAULT_PHASE_WALL_CLOCK_S = 1500.0
_DEFAULT_PILOT_PHASE_WALL_CLOCK_S = 5400.0
#: Leftover dashboard walls in this band cannot host a Claude→DeepSeek
#: C-BRIEF session. sess_b9fbae7 died at ~47s FAILED_BUDGET_SPENT after
#: COLLECTOR's in-process OpenRouter review; remap to stage 1 when the
#: DeepSeek CLI is ready. Explicit 0 still disables. Values above this
#: band (including leftover 7200) stay honoured.
_DEEPSEEK_LEFTOVER_WALL_MAX_S = 600.0

#: A build with no ledger event for this long has no process behind it.
#: An in-flight model_call NOTE is NOT a dead process — skip this stall
#: while a live WRITER / FACTORY_CODE_CLI worker is still inside its
#: deadline (20–40 min writes are legitimate). A calling-NOTE with no
#: live worker is an orphan (deploy / restart) and must not claim
#: ``model_call_in_progress``. Overdue calls use ``_model_call_overdue``.
_STALL_AFTER_S = 1800.0

#: Honesty when a model_call NOTE outlived the worker that wrote it.
#: sess_05914670d8f34533 (estate-management, tip d4a4029 / #382 deploy)
#: sat Building / model_call_in_progress for the leftover 7230s wall
#: after instance churn 7bxn4 → t5hm8 → 9lcrr killed the WRITER thread.
FACTORY_CODE_CLI_ORPHANED = "FACTORY_CODE_CLI_ORPHANED"

#: Quieter than a dead process: one coder call can sit in the model for
#: ~2–3 min with no NOTE. Past this the UI says "quiet" so a customer can
#: tell a long model call from a frozen 2/5 with no name. "may still be
#: running" is only honest while a model_call NOTE is inside its deadline.
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
            value = float(raw)
        except ValueError:
            value = None
        else:
            if value == 0:
                return 0.0
            if 0 < value <= _DEEPSEEK_LEFTOVER_WALL_MAX_S:
                from app.factory.build.coder_session import deepseek_cli_ready
                from app.factory.build.roles_handlers import writer_uses_codewhale

                if deepseek_cli_ready() or writer_uses_codewhale(None):
                    return _DEFAULT_WALL_CLOCK_S
            return value
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


def _phase_wall_clock_s(cycle: str = "code", auto_pilot: bool = False) -> float:
    """Per-role cap. Code-only stays 25 min; Store-green uses 90 min.

    A dashboard leftover of 1500s must not cap a keyed auto-pilot WRITER
    — that is how a 2-hour coding run died after the first handler wave.
    Explicit ``0`` still disables the bound. Values above the code-only
    default are honoured as an operator override.
    """
    raw = os.getenv(BUILD_PHASE_WALL_ENV)
    configured: Optional[float] = None
    if raw is not None:
        try:
            configured = float(raw)
        except ValueError:
            configured = None
    if _uses_pilot_budget(cycle, auto_pilot):
        if configured == 0:
            return 0.0
        if configured is None or configured <= _DEFAULT_PHASE_WALL_CLOCK_S:
            return _DEFAULT_PILOT_PHASE_WALL_CLOCK_S
        return configured
    if configured is not None:
        return configured
    return _DEFAULT_PHASE_WALL_CLOCK_S


def _phase_ref(role: Any) -> Dict[str, str]:
    """Named phase for the Floor: id plus the job title, not just 2/5."""
    from app.factory.build.authority import BuildRole, role_contract

    resolved = role if hasattr(role, "value") else BuildRole(role)
    return {"id": resolved.value, "label": role_contract(resolved).title}


def _open_model_call_note(activity_notes: Any) -> Any:
    """Latest in-flight model_call NOTE. CLI stdout must not drop the wall.

    A finished / hung-killed CLI NOTE closes the call so Floor does not
    keep 'inside watchdog' after harvest or HUNG_KILLED_BY_WALL.
    """
    for note in reversed(list(activity_notes or ())):
        detail = str(getattr(note, "detail", "") or "")
        payload = getattr(note, "payload", None) or {}
        if "FACTORY_CODE_CLI session finished" in detail:
            return None
        if "FACTORY_CODE_CLI_HUNG_KILLED_BY_WALL" in detail:
            return None
        if "budget wall — stopping CLI session" in detail:
            return None
        if payload.get("model_call"):
            return note
    return None


def _model_call_fields(
    last_note: Any, *, worker_live: bool = True
) -> Dict[str, Any]:
    """Surface an in-flight coder call so the Floor can bound 'quiet' copy.

    A ledger ``model_call`` NOTE is not a live worker. After a deploy the
    NOTE remains and the thread is gone — claiming in-progress then is
    the sess_05914670d8f34533 zombie (Building until the 7230s wall).
    """
    payload = (getattr(last_note, "payload", None) or {}) if last_note else {}
    if not payload.get("model_call"):
        return {}
    if not worker_live:
        return {}
    try:
        from app.factory.llm_watchdog import attempt_wall_s

        deadline = float(payload.get("deadline_s") or attempt_wall_s())
    except (TypeError, ValueError):
        from app.factory.llm_watchdog import attempt_wall_s

        deadline = attempt_wall_s()
    return {
        "model_call_in_progress": True,
        "model_call_deadline_s": round(deadline, 1),
    }


def _orphaned_inflight_model_call(
    calling_note: Any, product_id: str, *, worker_live: Optional[bool] = None
) -> bool:
    """True when a model_call NOTE has no live runner / CLI worker."""
    payload = (getattr(calling_note, "payload", None) or {}) if calling_note else {}
    if not payload.get("model_call"):
        return False
    live = _live_runner_thread(product_id) if worker_live is None else worker_live
    return not live


def _model_call_overdue(last_note: Any, idle_s: float) -> Optional[str]:
    """Concrete timeout detail when a calling-NOTE outlived its watchdog wall."""
    fields = _model_call_fields(last_note, worker_live=True)
    if not fields:
        return None
    deadline = float(fields["model_call_deadline_s"])
    # Prefer the calling NOTE's own age. A later ledger event (or a
    # touched file mtime) must not reset the wall — that is how the
    # Floor can keep saying "may still be running" past the deadline
    # while WRITER is stuck in one model call.
    note_age = _event_age_s(getattr(last_note, "ts", "") or "", idle_s)
    age = max(float(idle_s), float(note_age))
    if age <= deadline:
        return None
    what = getattr(last_note, "detail", None) or "coder LLM call"
    return (
        f"coder LLM timed out after {int(age)}s "
        f"(deadline {int(deadline)}s) — {what}"
    )


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


CRASH_MARKER_NAME = ".build_thread_crashed"


def _crash_marker_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / CRASH_MARKER_NAME


def _write_crash_marker(output_dir: Path | str, detail: str) -> None:
    try:
        _crash_marker_path(output_dir).write_text(detail.strip() + "\n", encoding="utf-8")
    except OSError:
        logger.exception("could not write crash marker at %s", output_dir)


def _live_runner_thread(product_id: str) -> bool:
    name = f"build-{product_id}"
    for thread in threading.enumerate():
        if thread.name == name and thread.is_alive():
            return True
    return False


def _product_id_of(events: Any, output_dir: Path | str) -> str:
    from app.factory.build.ledger import EventKind

    for event in events or ():
        if event.kind is EventKind.RUN_STARTED:
            pid = str((event.payload or {}).get("product_id") or "").strip()
            if pid:
                return pid
    return Path(output_dir).name


_RUN_SUFFIX_RE = re.compile(r"__run(\d+)$")


def next_fresh_output(requested: Path | str) -> Path:
    """Sibling workspace that does not reuse a terminal-failed ledger.

    ``sessions/{id}/{product_id}`` is the first run. After RUN_FAILED the
    same path would resume a dead ledger (exhausted rework, TESTER still
    red) and the Floor would stay stopped. ``{product_id}__run2`` is a
    new auto-pilot cycle with a reset budget.
    """
    requested = Path(requested)
    parent = requested.parent
    stem = _RUN_SUFFIX_RE.sub("", requested.name)
    n = 1
    while True:
        candidate = parent / (stem if n == 1 else f"{stem}__run{n}")
        if not _ledger_path(candidate).is_file():
            return candidate
        n += 1
        if n > 10_000:
            return parent / f"{stem}__run{n}"


def _with_level_grade(status: Dict[str, Any], output_dir: Path | str) -> Dict[str, Any]:
    """Attach a fail-closed Level grade. Import late to avoid a cycle."""
    from app.factory.build.level_grade import attach_level_grade

    return attach_level_grade(status, output_dir)


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
    try:
        from app.factory.build.auto_pilot import factory_auto_pilot_enabled

        auto = bool(factory_auto_pilot_enabled())
    except Exception:  # noqa: BLE001
        auto = False
    return {"cycle": cycle or "code", "pilot_ready": ready, "auto_pilot": auto}


def _authorship(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Dict[str, Any]:
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
    from app.factory.build.authorship import (
        cli_authored_ids_from,
        coding_agent_artifact_ids,
        is_action_artifact_id,
        kept_handler_ids_from,
        n_required_capabilities_from,
        writer_authorship_counts,
    )

    sources = prov.get("artifact_sources") or {}
    counts = writer_authorship_counts(sources)
    agent = coding_agent_artifact_ids(sources)
    action_ids = [cid for cid in agent if is_action_artifact_id(cid)]
    dispatch = prov.get("brief_dispatch") or {}
    cli_ids = cli_authored_ids_from(dispatch)
    failures = prov.get("coder_failures") or {}
    n_required = n_required_capabilities_from(
        prov, output_dir, state=dispatch, blueprint=blueprint, plan=plan
    )
    authorship = {
        **counts,
        "agent_artifacts": agent,
        "action_py": len(action_ids),
        "cli_authored_ids": list(cli_ids or []),
        "kept_handler_ids": kept_handler_ids_from(dispatch),
        # Named, not counted: "3 stubs" tells the customer nothing about
        # which parts of their platform are degraded.
        "coder_failures": {k: str(v)[:300] for k, v in failures.items()},
    }
    if n_required is not None:
        authorship["n_required"] = n_required
    return {"authorship": authorship}


def _thin_authorship_detail(detail: Any) -> bool:
    text = str(detail or "")
    return "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in text


def _three_gate_success_detail(cycle: str) -> str:
    from app.factory.build.product_gate import GATE_SCOPES

    code_line = "CODE PASS — %s" % GATE_SCOPES["CODE"]
    if str(cycle or "").strip().lower() != "pilot":
        return "; ".join(
            (
                code_line,
                "PRODUCT NOT RUN — %s" % GATE_SCOPES["PRODUCT"],
                "STORE NOT RUN — %s" % GATE_SCOPES["STORE"],
            )
        )
    return "; ".join(
        (
            code_line,
            "PRODUCT PASS — %s" % GATE_SCOPES["PRODUCT"],
            "STORE PASS — %s" % GATE_SCOPES["STORE"],
        )
    )


def _reevaluate_thin_authorship_failure(
    status: Dict[str, Any],
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Dict[str, Any]:
    """Recompute the live floor on a sticky THIN_AUTHORSHIP RUN_FAILED.

    ``_finish`` replaces the three-gate SUCCESS sentence with the refuse
    string. After #391 a 4-cap golden with 4 authored ids must not stay
    locked on a baked ``need ≥5`` detail when n_required is knowable.
    """
    if not _thin_authorship_detail(status.get("detail")):
        return status
    from app.factory.build.authorship import full_pilot_authorship_from

    floor = full_pilot_authorship_from(
        status, output_dir, plan=plan, blueprint=blueprint
    )
    if floor.meets_floor:
        cycle = str(status.get("cycle") or "code").strip().lower() or "code"
        status["state"] = "succeeded"
        status["outcome"] = "SUCCESS"
        status["cycle"] = cycle
        status["pilot_ready"] = cycle == "pilot"
        status["detail"] = _three_gate_success_detail(cycle)
        status["honesty"] = "full_pilot_authorship_reevaluated"
        return status
    if floor.below_floor:
        n_req = (
            f", n_required={floor.n_required}"
            if floor.n_required is not None
            else ""
        )
        status["detail"] = (
            "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the "
            f"full-pilot floor (action_py={floor.action_py}, "
            f"cli_authored_ids={len(floor.cli_authored_ids)}, "
            f"need ≥{floor.need}{n_req}) — will not ship a "
            "Store-green / full-pilot zip"
        )
    return status


def _phase_trail(
    events: Sequence[Any],
) -> tuple[list, "dict | None"]:
    """F3: per-phase trail from ledger events, plus the first failure.

    One row per build phase in BUILD_PHASES order — never one concatenated
    string. A row carries outcome, named reason token, location, timestamp
    and detail so the Floor can render "WRITER failed — writer_no_output".
    """
    from app.factory.build.authority import BUILD_PHASES
    from app.factory.build.sanitize import sanitize_for_status

    phases = [p.value for p in BUILD_PHASES]
    state: Dict[str, Dict[str, Any]] = {
        p: {
            "phase": p,
            "outcome": "not_reached",
            "reason": "",
            "location": "",
            "timestamp": None,
            "detail": "",
        }
        for p in phases
    }
    failure: Optional[Dict[str, Any]] = None
    for event in events:
        role = getattr(event, "role", None)
        kind = getattr(event, "kind", None)
        if role is None:
            continue
        role_name = role.value if hasattr(role, "value") else str(role)
        kind_name = kind.value if hasattr(kind, "value") else str(kind)
        entry = state.get(role_name)
        if entry is None:
            continue
        if kind_name == "PHASE_STARTED":
            entry["outcome"] = "running"
            entry["timestamp"] = event.ts
        elif kind_name == "GATE_PASSED":
            entry["outcome"] = "passed"
            entry["timestamp"] = event.ts
        elif kind_name in ("GATE_FAILED", "PHASE_ABORTED"):
            payload = event.payload or {}
            entry["outcome"] = "failed" if kind_name == "GATE_FAILED" else "aborted"
            entry["reason"] = sanitize_for_status(payload.get("reason")) or ""
            entry["location"] = sanitize_for_status(payload.get("location")) or role_name
            entry["timestamp"] = event.ts
            entry["detail"] = sanitize_for_status(event.detail) or ""
            if failure is None:
                failure = dict(entry)
    return [state[p] for p in phases], failure


def _crash_failure(trail: list) -> "Dict[str, Any] | None":
    """F: a crashed thread must still name the phase it died in.

    The crash handler records RUN_FAILED with no per-phase verdict, so the
    trail's last started phase reads 'running' forever. Mark it aborted
    with a named reason and return it as the failure the Floor renders.
    """
    running = next(
        (row for row in trail if row.get("outcome") == "running"), None
    )
    if running is None:
        return None
    running["outcome"] = "aborted"
    running["reason"] = "build_thread_crashed"
    running["location"] = running.get("location") or running.get("phase") or ""
    running["detail"] = "build thread crashed; see service logs"
    return dict(running)


def build_status(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Dict[str, Any]:
    """Read the build's state off disk.

    Deliberately reads the LEDGER rather than any in-process registry: the
    ledger is fsynced per event by the runner, so this answer survives a
    worker restart and is the same answer the shipped artifact carries.
    """
    from app.factory.build.authority import BUILD_PHASES
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.sanitize import sanitize_for_status

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
        quarantined = ledger.quarantined_notes()
    except Exception as exc:  # noqa: BLE001 -- a torn ledger must not 500
        from app.factory.build.ledger import LEDGER_UNREADABLE

        logger.warning("unreadable build ledger at %s: %s", path, exc)
        # unknown is "no ledger yet" (template / awaitBuild → succeeded).
        # An unreadable ledger is a crashed run — Floor must not keep
        # "CODING AGENT HAS TAKEN OVER" / Platforms "Building…".
        return _with_level_grade(
            {
                "state": "failed",
                "detail": f"{LEDGER_UNREADABLE}: {exc}",
                "pilot_ready": False,
                "honesty": LEDGER_UNREADABLE,
            },
            output_dir,
        )

    phases = [p.value for p in BUILD_PHASES]
    current_role = interrupted or resume
    if terminal is None and _crash_marker_path(output_dir).is_file():
        trail, failure = _phase_trail(events)
        failure = failure or _crash_failure(trail)
        return _with_level_grade(
            {
                "state": "failed",
                "detail": "build thread crashed; see service logs",
                "pilot_ready": False,
                "honesty": "BUILD_THREAD_CRASHED",
                # F: the Floor must still name the phase that died.
                "phase_trail": trail,
                "failure": failure,
            },
            output_dir,
        )
    if (
        terminal is None
        and interrupted is not None
        and quarantined
        and not _live_runner_thread(_product_id_of(events, output_dir))
    ):
        from app.factory.build.ledger import LEDGER_EXTERNAL_NOTE_QUARANTINED

        # Live sess_69f28c0d8bc540e9: CLI scribble bricked append, the
        # crash handler could not write RUN_FAILED, the thread died, and
        # Floor kept "taken over / 1/5". After quarantine the ledger is
        # readable again — still not an active coding claim.
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    f"{LEDGER_EXTERNAL_NOTE_QUARANTINED}: seq-less NOTE in "
                    "the ledger and the build thread is gone — not still coding"
                ),
                "pilot_ready": False,
                "honesty": LEDGER_EXTERNAL_NOTE_QUARANTINED,
                "ledger_quarantined_notes": quarantined,
            },
            output_dir,
        )

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
    inspects = [
        e
        for e in notes
        if (e.payload or {}).get("budget_inspect")
        or (e.payload or {}).get("kind") == "budget_inspect"
    ]
    last_inspect = (inspects[-1].payload or {}) if inspects else None
    activity_notes = [e for e in notes if e not in inspects]
    last_note = activity_notes[-1] if activity_notes else None
    calling_note = _open_model_call_note(activity_notes)
    product_id = _product_id_of(events, output_dir)
    worker_live = _live_runner_thread(product_id)
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
        # F5: keys and auth headers are never rendered to the Floor.
        "last_event": sanitize_for_status(
            (last_note or last_any).detail if (last_note or last_any) else None
        ),
        "last_event_at": last_any.ts if last_any else None,
        "last_event_age_s": round(idle_s, 1),
        "stale": idle_s > _STALE_AFTER_S,
        **_model_call_fields(calling_note or last_note, worker_live=worker_live),
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

    from app.factory.build.coder_session_status import session_status

    phase_trail, failure = _phase_trail(events)
    # F: a crashed thread (RUN_FAILED with no per-phase verdict) must still
    # name the phase it died in — otherwise the Floor cannot say 'why'.
    if failure is None and terminal is not None and terminal.kind is EventKind.RUN_FAILED:
        tp = terminal.payload or {}
        if not tp.get("reason") or tp.get("reason") == "build_thread_crashed":
            failure = _crash_failure(phase_trail)
            if failure is not None:
                # The crash handler stamps the actual exception into the
                # terminal detail — show it, not the generic marker text.
                failure["detail"] = sanitize_for_status(terminal.detail) or (
                    failure.get("detail") or ""
                )
    progress = {
        "phases": phases,
        "completed": [p for p in phases if p in completed],
        "phases_total": len(phases),
        "phases_done": sum(1 for p in phases if p in completed),
        "ledger_quarantined_notes": quarantined,
        # F3: per-phase outcome trail + the failure's exact location, named.
        "phase_trail": phase_trail,
        "failure": failure,
        **monitor,
        **_cycle_fields(ledger, terminal),
        **session_status(Path(output_dir)),
    }
    if last_inspect:
        from app.factory.build.budget_inspect import (
            reconcile_budget_inspect_after_success,
        )

        ready_now = False
        if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
            try:
                ready_now = bool(ledger.pilot_ready())
            except Exception:  # noqa: BLE001
                ready_now = False
        progress["budget_inspect"] = (
            reconcile_budget_inspect_after_success(
                last_inspect, pilot_ready=True
            )
            if ready_now
            else last_inspect
        )

    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        payload = terminal.payload or {}
        succeeded = {
            "state": "succeeded",
            "detail": terminal.detail,
            "cycle": payload.get("cycle") or "code",
            "outcome": payload.get("outcome"),
            # Only a SUCCESS that closed a pilot cycle is Store-green / pilot-ready.
            # Code-phase success must not be presented as a finished pilot.
            "pilot_ready": ledger.pilot_ready(),
            **progress,
            **_authorship(output_dir, blueprint=blueprint, plan=plan),
            "stale": False,
        }
        if payload.get("honesty"):
            succeeded["honesty"] = payload.get("honesty")
        if payload.get("score"):
            succeeded["n3_score"] = payload.get("score")
        if payload.get("builds_sha"):
            succeeded["builds_sha"] = payload.get("builds_sha")
        return _with_level_grade(succeeded, output_dir)
    if terminal is not None and terminal.kind is EventKind.RUN_FAILED:
        payload = terminal.payload or {}
        from app.factory.build.n3_store_gate import handoff_awaiting_n3

        if handoff_awaiting_n3(output_dir):
            # Receipt accepted; N3 store-gate is the next green. Do not paint
            # this as a terminal coding failure or unlock package.
            waiting = {
                "state": "building",
                "detail": (
                    "HANDOFF_TO_N3: waiting for cerebrum-builds "
                    "store-gate 12/12"
                ),
                "cycle": payload.get("cycle") or "code",
                "outcome": "HANDOFF_TO_N3",
                "honesty": "HANDOFF_TO_N3",
                "next": "n3_gate",
                "green": False,
                "n3_waiting": True,
                "pilot_ready": False,
                "findings": list(payload.get("findings") or [])[:10],
                **progress,
                **_authorship(output_dir, blueprint=blueprint, plan=plan),
                "stale": False,
            }
            for key in (
                "builds_sha",
                "builds_branch",
                "builds_owner",
                "builds_repo",
            ):
                if payload.get(key):
                    waiting[key] = payload[key]
            return _with_level_grade(waiting, output_dir)
        failed = {
            "state": "failed",
            "detail": terminal.detail,
            "cycle": payload.get("cycle") or "code",
            "outcome": payload.get("outcome"),
            "pilot_ready": False,
            "findings": list(payload.get("findings") or [])[:10],
            **progress,
            **_authorship(output_dir, blueprint=blueprint, plan=plan),
            "stale": False,
        }
        if payload.get("honesty"):
            failed["honesty"] = payload.get("honesty")
        if _thin_authorship_detail(terminal.detail):
            failed = _reevaluate_thin_authorship_failure(
                failed, output_dir, blueprint=blueprint, plan=plan
            )
        return _with_level_grade(failed, output_dir)
    # Intra-phase activity. Without this a WRITER pass of ~16 agent calls
    # reports a frozen "2/5" for twenty minutes and a customer cannot tell
    # work from a hang.
    activity: Dict[str, Any] = {}
    if last_note is not None:
        activity = {
            "activity": sanitize_for_status(last_note.detail),
            "activity_stage": (last_note.payload or {}).get("stage"),
            "activity_done": (last_note.payload or {}).get("done"),
            "activity_total": (last_note.payload or {}).get("total"),
        }

    # A calling-NOTE that outlived the watchdog wall means the WRITER thread
    # is stuck in a model call that will never complete. Fail now so the
    # Floor shows CODING AGENT STOPPED instead of "quiet for N min".
    overdue = _model_call_overdue(calling_note or last_note, idle_s)
    if overdue:
        return _with_level_grade(
            {
                "state": "failed",
                "detail": overdue,
                "pilot_ready": False,
                **progress,
                **activity,
                "stale": False,
                "model_call_in_progress": False,
            },
            output_dir,
        )

    # Deploy / worker restart mid-WRITER leaves the model_call NOTE and
    # the 7230s C-BRIEF wall, but no thread and no FACTORY_CODE_CLI.
    # Do not wait for the deadline — that is the estate-management zombie.
    if _orphaned_inflight_model_call(
        calling_note or last_note, product_id, worker_live=worker_live
    ):
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    f"{FACTORY_CODE_CLI_ORPHANED}: no live WRITER / "
                    "FACTORY_CODE_CLI worker after process restart — "
                    "model call is not in progress"
                ),
                "pilot_ready": False,
                "honesty": FACTORY_CODE_CLI_ORPHANED,
                **progress,
                **activity,
                "model_call_in_progress": False,
            },
            output_dir,
        )

    # A build whose thread died (worker restart, OOM, redeploy) leaves the
    # ledger's last event as PHASE_STARTED forever, which read as "building"
    # for eternity. Age the file: no event for this long means nothing is
    # working on it, and saying so is the honest answer.
    # An in-flight coder call inside its deadline is work, not a dead
    # process — stalling at 30 min would abort a legitimate 40 min write.
    in_flight_call = bool(
        _model_call_fields(calling_note or last_note, worker_live=worker_live)
    )
    if idle_s > _STALL_AFTER_S and not in_flight_call:
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    f"no build activity for {int(idle_s // 60)} min — the build "
                    "process is gone (restart or redeploy); generate again"
                ),
                "pilot_ready": False,
                **progress,
                **activity,
            },
            output_dir,
        )

    return _with_level_grade(
        {"state": "building", "detail": "build in progress", **progress, **activity},
        output_dir,
    )


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
    tenant_store: Any = None,
    brief: str = "",
) -> None:
    from app.factory.build.auto_pilot import factory_auto_pilot_enabled
    from app.factory.build.runner import BuildBudget, RoleRunner

    auto = cycle == "code" and factory_auto_pilot_enabled()
    # THIS BUILD's session id, resolved into a LOCAL and threaded through the
    # runner — never written back into os.environ.
    #
    # This used to do `os.environ["FACTORY_SESSION_ID"] = sid` from inside the
    # per-build THREAD, and nothing ever cleared it. Two ways that is wrong,
    # and only one of them needs concurrency:
    #   LOST WRITE (already live at a cap of 1): build A sets the global and
    #     never unsets it, so build B finds it non-empty, takes the `if not`
    #     branch as false, and runs its whole lifetime under A's session id.
    #     Every build after the first in a process inherited the first one's.
    #   CLOBBER (needs concurrency): two builds both find it empty, both
    #     write, last writer wins for both.
    # The session id is per-build state and the process environment is not a
    # place to keep per-build state in a multi-tenant process. An externally
    # set FACTORY_SESSION_ID is still honoured — it is a deliberate operator
    # override — it is just no longer written by us.
    from app.factory.build.orphan_recovery import session_id_from_output

    session_id = (
        str(os.getenv("FACTORY_SESSION_ID") or "").strip()
        or session_id_from_output(output_dir)
        or ""
    )
    try:
        runner = RoleRunner(
            blueprint,
            output_dir,
            blocks_root=blocks_root,
            budget=BuildBudget(
                max_rework=_max_rework(cycle, auto_pilot=auto),
                wall_clock_s=_wall_clock_s(cycle, auto_pilot=auto),
                phase_wall_clock_s=_phase_wall_clock_s(
                    cycle, auto_pilot=auto
                ),
            ),
            cycle=cycle,
            auto_pilot=auto if cycle == "code" else False,
            tenant_store=tenant_store,
            brief=brief,
            session_id=session_id,
        )
        outcome = runner.run()
        logger.info(
            "runner build finished: product=%s outcome=%s rework=%s",
            getattr(blueprint, "product_id", "unknown"),
            outcome.outcome.value if hasattr(outcome.outcome, "value") else outcome.outcome,
            outcome.rework_used,
        )
        from app.factory.build.runner import Outcome as RunnerOutcome

        if outcome.outcome is RunnerOutcome.HANDOFF_TO_N3:
            # Receipt accepted after TESTER + STORE_MANAGER. N3 store-gate
            # is next. Keep the generation charge (not a fail) and do not
            # clone a non-green Steward tree. Poll cerebrum-builds commit
            # status in this same thread — never re-enter WRITER or launch
            # another Background Agent.
            _clear_quota_marker(output_dir)
            try:
                from app.factory.build.n3_store_gate import wait_and_ingest_n3

                wait_and_ingest_n3(output_dir)
            except Exception:  # noqa: BLE001 — waiter crash must not kill the thread
                logger.exception("n3 store-gate ingest failed for %s", output_dir)
            return
    except Exception as exc:  # noqa: BLE001
        # The thread must never die silently: without this the ledger's last
        # event stays PHASE_STARTED and status reads "building" forever.
        logger.exception("runner build crashed for %s", output_dir)
        from app.factory.build.sanitize import sanitize_for_status

        crash_detail = "build thread crashed: " + sanitize_for_status(
            str(exc) or type(exc).__name__
        )
        crash_detail = crash_detail[:400]
        try:
            from app.factory.build.ledger import BuildLedger, EventKind

            ledger = BuildLedger(_ledger_path(output_dir))
            crashed_role = ledger.interrupted_role()
            ledger.append(
                EventKind.RUN_FAILED,
                detail=crash_detail,
                payload={
                    "reason": "build_thread_crashed",
                    "location": crashed_role.value if crashed_role else "",
                    "exception": sanitize_for_status(type(exc).__name__),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not record the crash in the ledger")
            _write_crash_marker(output_dir, crash_detail)
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
    tenant_identity: Optional[str] = None,
    brief: str = "",
) -> Dict[str, Any]:
    """Start a background runner build and return immediately.

    Returns the same keys the template path returns (``output_dir``,
    ``product_id``, ``inputs_hash``) so callers and stored session state do
    not need a second shape, plus ``build`` carrying the live status. The
    hash is the BLUEPRINT hash: the output tree does not exist yet, and
    keying on it -- as ``ProductGenerator.inputs_hash`` does -- would make
    resume impossible.
    """
    from app.factory.build.coder_session import (
        CodeCliUnavailable,
        OWNER_GATED_CLI_LOG,
        raise_if_cli_session_unready,
    )
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
    fresh_workspace = False
    if ledger.exists():
        status = build_status(out)
        if status.get("state") == "building":
            from app.factory.build.n3_store_gate import (
                handoff_awaiting_n3,
                n3_ingest_live,
                start_n3_ingest_job,
            )

            if handoff_awaiting_n3(out):
                if n3_ingest_live(out):
                    logger.info(
                        "refusing second N3 ingest; store-gate waiter already live at %s",
                        out,
                    )
                    return {
                        "engine": RUNNER,
                        "output_dir": str(out),
                        "product_id": getattr(blueprint, "product_id", "unknown"),
                        "inputs_hash": inputs_hash,
                        "build": status,
                        "cycle": resolved,
                        "already_running": True,
                        "n3_waiting": True,
                    }
                started = start_n3_ingest_job(out)
                logger.info(
                    "HANDOFF_TO_N3 at %s; %s store-gate ingest (no WRITER / BA)",
                    out,
                    "started" if started else "attached to",
                )
                return {
                    "engine": RUNNER,
                    "output_dir": str(out),
                    "product_id": getattr(blueprint, "product_id", "unknown"),
                    "inputs_hash": inputs_hash,
                    "build": build_status(out, blueprint=blueprint),
                    "cycle": resolved,
                    "already_running": not started,
                    "n3_waiting": True,
                    "n3_ingest": True,
                }
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
        if (
            status.get("state") == "stalled"
            and status.get("honesty") == FACTORY_CODE_CLI_ORPHANED
        ):
            logger.info(
                "resuming FACTORY_CODE_CLI_ORPHANED ledger at %s "
                "(no live WRITER / FACTORY_CODE_CLI worker)",
                out,
            )
        if status.get("state") == "failed":
            # A terminal RUN_FAILED / rework-exhausted ledger is not a
            # resume source. Same-hash generate would otherwise attach to
            # the dead run and the Floor would stay CODING AGENT STOPPED.
            fresh = next_fresh_output(out)
            logger.info(
                "terminal ledger at %s; starting fresh workspace at %s",
                out,
                fresh,
            )
            out = fresh
            out.mkdir(parents=True, exist_ok=True)
            ledger = BuildLedger(_ledger_path(out))
            fresh_workspace = True

    try:
        raise_if_cli_session_unready()
    except CodeCliUnavailable:
        logger.error("%s", OWNER_GATED_CLI_LOG)
        raise

    if not ledger.exists():
        ledger.start_run(
            product_id=getattr(blueprint, "product_id", "unknown"),
            inputs_hash=inputs_hash,
        )

    _write_quota_marker(out, quota_account_id)

    # Phase 1 tenant isolation: bind the store handle from the
    # authenticated identity BEFORE the thread starts. An unauthenticated
    # build gets None and the worker refuses (no_authenticated_tenant)
    # instead of running unbound. tenant_identity wins when present
    # (account id for account callers; the server-owned session id for
    # master-key/admin callers, which carry no account).
    from app.factory.build.tenant_bind import bind_tenant_store

    identity = tenant_identity if tenant_identity is not None else quota_account_id
    tenant_store = bind_tenant_store(identity)

    thread = threading.Thread(
        target=_run,
        args=(
            blueprint,
            out,
            Path(blocks_root) if blocks_root else None,
            resolved,
            tenant_store,
            str(brief or "").strip(),
        ),
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
        "fresh_workspace": fresh_workspace,
    }
