"""Recover WRITER / FACTORY_CODE_CLI model calls orphaned by process restart.

Live sess_05914670d8f34533 (estate-management, Estate Steward, tip d4a4029 /
#382 deploy dep-daf3htmq at 2026-09-07T04:09:26Z) froze at WRITER dispatch:

* state=building, phases_done=2 (COLLECTOR+CLONER), current_phase=WRITER
* model_call_in_progress=true, model_call_deadline_s=7230
* last_event_at frozen at 04:09:21Z; coder_brief / coder_log absent
* instance churn 7bxn4 → t5hm8 → 9lcrr killed the runner thread

The ledger NOTE is not a live worker. On boot, resume the gated C-BRIEF
(RoleRunner re-enters interrupted WRITER — one compiled brief, not a
per-capability handle() loop) when a blueprint is available; otherwise
fail closed with ``FACTORY_CODE_CLI_ORPHANED``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cerebrumdev.factory.orphan_recovery")

ORPHAN_FAIL_DETAIL = (
    "FACTORY_CODE_CLI_ORPHANED: WRITER / FACTORY_CODE_CLI model call has no "
    "live worker after process restart — coding agent stopped (not in progress)"
)
ORPHAN_RESUME_NOTE = (
    "resuming orphaned WRITER C-BRIEF after process restart "
    "(FACTORY_CODE_CLI_ORPHANED)"
)


def iter_session_build_dirs(outputs_root: Optional[Path] = None) -> List[Path]:
    """``factory_outputs/sessions/{session_id}/{product_id}`` workspaces."""
    from app.factory.paths import factory_outputs_root

    root = Path(outputs_root) if outputs_root is not None else factory_outputs_root()
    sessions = root / "sessions"
    if not sessions.is_dir():
        return []
    found: List[Path] = []
    for session_dir in sorted(sessions.iterdir()):
        if not session_dir.is_dir():
            continue
        for product_dir in sorted(session_dir.iterdir()):
            if (product_dir / "build_ledger.jsonl").is_file():
                found.append(product_dir)
    return found


def session_id_from_output(output_dir: Path | str) -> Optional[str]:
    parts = Path(output_dir).resolve().parts
    try:
        idx = parts.index("sessions")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def _storage_sessions_root() -> Path:
    return Path(os.getenv("STORAGE_PATH", "./storage")) / "sessions"


def load_blueprint_for_workspace(
    output_dir: Path | str,
    *,
    storage_root: Optional[Path] = None,
) -> Optional[Any]:
    """Session snapshot first, then workspace ``docs/blueprint/`` copy."""
    from app.factory.blueprint import ProductBlueprint

    out = Path(output_dir)
    session_id = session_id_from_output(out)
    roots: List[Path] = []
    if storage_root is not None:
        roots.append(Path(storage_root) / "sessions")
    roots.append(_storage_sessions_root())
    if session_id:
        for root in roots:
            path = root / session_id / "state.json"
            bp = _blueprint_from_state_path(path)
            if bp is not None:
                return bp
        try:
            from app.core.session_persistence import load_session_state

            state = load_session_state(session_id)
        except Exception:  # noqa: BLE001
            state = None
        if state is not None:
            raw = getattr(getattr(state, "product_design", None), "blueprint", None)
            if isinstance(raw, dict) and raw:
                try:
                    return ProductBlueprint.model_validate(raw)
                except Exception:  # noqa: BLE001
                    logger.warning("session %s blueprint failed validation", session_id)
    for rel in (
        Path("docs") / "blueprint" / "product_blueprint.json",
        Path("docs") / "product_blueprint.json",
    ):
        path = out / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            try:
                return ProductBlueprint.model_validate(data)
            except Exception:  # noqa: BLE001
                continue
    return None


def _blueprint_from_state_path(path: Path) -> Optional[Any]:
    from app.factory.blueprint import ProductBlueprint

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = (data.get("product_design") or {}).get("blueprint")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return ProductBlueprint.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def is_orphaned_inflight_workspace(output_dir: Path | str) -> bool:
    """Non-terminal ledger with an open model_call and no live worker."""
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import (
        _ledger_path,
        _live_runner_thread,
        _open_model_call_note,
        _product_id_of,
    )

    path = _ledger_path(output_dir)
    if not path.is_file():
        return False
    try:
        ledger = BuildLedger(path)
        events = ledger.events()
        if ledger.terminal_event() is not None:
            return False
        notes = [e for e in events if e.kind is EventKind.NOTE]
        inspects = {
            e
            for e in notes
            if (e.payload or {}).get("budget_inspect")
            or (e.payload or {}).get("kind") == "budget_inspect"
        }
        activity = [e for e in notes if e not in inspects]
        calling = _open_model_call_note(activity)
        if calling is None:
            return False
        product_id = _product_id_of(events, output_dir)
        return not _live_runner_thread(product_id)
    except Exception:  # noqa: BLE001 — torn ledger is not a resume source
        logger.warning("could not inspect ledger at %s", output_dir, exc_info=True)
        return False


def fail_orphaned_model_call(
    output_dir: Path | str, *, detail: Optional[str] = None
) -> Dict[str, Any]:
    """Write RUN_FAILED so Floor shows coder-stopped, not a 7230s zombie."""
    from app.factory.build.coder_session import NAMED_BLOCKER_CLI_ORPHANED
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import FACTORY_CODE_CLI_ORPHANED, _ledger_path

    text = (detail or ORPHAN_FAIL_DETAIL).strip()
    if NAMED_BLOCKER_CLI_ORPHANED not in text:
        text = f"{NAMED_BLOCKER_CLI_ORPHANED}: {text}"
    try:
        BuildLedger(_ledger_path(output_dir)).append(
            EventKind.RUN_FAILED,
            detail=text,
            payload={
                "outcome": "FAILED_ROLE_ERROR",
                "honesty": FACTORY_CODE_CLI_ORPHANED,
                "orphan_recovery": True,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("could not fail-close orphaned model call at %s", output_dir)
        return {"action": "error", "output_dir": str(output_dir), "detail": text}
    logger.error("failed closed orphaned FACTORY_CODE_CLI at %s: %s", output_dir, text)
    return {
        "action": "failed",
        "output_dir": str(output_dir),
        "detail": text,
        "honesty": FACTORY_CODE_CLI_ORPHANED,
    }


def resume_orphaned_model_call(
    output_dir: Path | str,
    blueprint: Any,
    *,
    cycle: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-enter RoleRunner at interrupted WRITER (gated C-BRIEF, not micro-fixer)."""
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import (
        FACTORY_CODE_CLI_ORPHANED,
        _ledger_path,
        start_runner_build,
    )

    out = Path(output_dir)
    try:
        BuildLedger(_ledger_path(out)).append(
            EventKind.NOTE,
            detail=ORPHAN_RESUME_NOTE,
            payload={
                "stage": "dispatch",
                "orphan_recovery": True,
                "honesty": FACTORY_CODE_CLI_ORPHANED,
            },
        )
    except Exception:  # noqa: BLE001 — resume must still start
        logger.exception("could not note orphan resume at %s", out)
    result = start_runner_build(blueprint, out, cycle=cycle)
    logger.info(
        "resumed orphaned WRITER at %s already_running=%s",
        out,
        result.get("already_running"),
    )
    return {
        "action": "resumed",
        "output_dir": str(result.get("output_dir") or out),
        "product_id": result.get("product_id"),
        "already_running": result.get("already_running"),
        "build": result.get("build"),
    }


def recover_orphaned_model_calls(
    *,
    outputs_root: Optional[Path] = None,
    storage_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Boot hook: resume or fail-close every orphaned in-flight model_call."""
    from app.factory.build.coder_session import CodeCliUnavailable

    results: List[Dict[str, Any]] = []
    for output_dir in iter_session_build_dirs(outputs_root):
        if not is_orphaned_inflight_workspace(output_dir):
            continue
        blueprint = load_blueprint_for_workspace(
            output_dir, storage_root=storage_root
        )
        if blueprint is None:
            results.append(
                fail_orphaned_model_call(
                    output_dir,
                    detail=(
                        f"{ORPHAN_FAIL_DETAIL}; no product_blueprint to resume "
                        f"C-BRIEF for {output_dir}"
                    ),
                )
            )
            continue
        try:
            results.append(resume_orphaned_model_call(output_dir, blueprint))
        except CodeCliUnavailable as exc:
            results.append(fail_orphaned_model_call(output_dir, detail=str(exc)))
        except Exception as exc:  # noqa: BLE001 — boot must not die
            logger.exception("orphan resume failed at %s", output_dir)
            results.append(
                fail_orphaned_model_call(
                    output_dir,
                    detail=f"{ORPHAN_FAIL_DETAIL}; resume raised {exc}",
                )
            )
    if results:
        logger.info(
            "orphan model_call recovery: %s",
            [(r.get("action"), r.get("output_dir")) for r in results],
        )
    return results
