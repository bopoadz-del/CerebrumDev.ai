"""Stop-and-inspect coder budget — staged walls, never a silent 2h burn.

Default path: start a ~30 minute stage, hard-stop, inspect the ledger /
workspace (which caps were written, contract misses, timeouts, stub rate,
why ``pilot_ready`` is still false), and only then decide whether to
continue with another gated brief at ~45 minutes.

A leftover ``FACTORY_BUILD_WALL_CLOCK_S=7200`` or an already-granted high
wall is honoured (observe/log, do not slash). The default is not 2 hours.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger("cerebrumdev.factory.budget_inspect")

#: First stage. Hard-stop here and inspect before any extra wall.
STAGE_1_S = 1800.0
#: Optional second stage — only after inspect says real work is progressing.
STAGE_2_S = 2700.0
#: Last-resort ceiling. Never the default; never granted silently.
CEILING_S = 7200.0

INSPECT_NOTE_KIND = "budget_inspect"


def inspect_build(
    ledger: Any,
    workspace: Any = None,
    state: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Snapshot of what the run has achieved so far.

    Reads the ledger first. Provenance on disk fills authorship when the
    WRITER has already written ``docs/build_provenance.json``.
    """
    events = list(getattr(ledger, "events", lambda: ())())
    caps_written: List[str] = []
    caps_templated: List[str] = []
    timeouts: List[str] = []
    contract_misses: List[str] = []
    current_capability = ""
    rework = 0
    model_call = ""
    phase_done = 0
    phase_total = 0

    for event in events:
        payload = getattr(event, "payload", None) or {}
        detail = str(getattr(event, "detail", "") or "")
        kind = getattr(getattr(event, "kind", None), "value", event.kind)
        cap = str(payload.get("capability") or "").strip()
        source = str(payload.get("source") or "")
        stage = str(payload.get("stage") or "")

        if cap and stage in {"handlers", "routes", "models", "coder"}:
            current_capability = cap
            if "factory-grounded" in source.lower():
                if cap not in caps_written:
                    caps_written.append(cap)
            elif source.startswith("coder LLM") or source.startswith("coder CLI"):
                if cap not in caps_written:
                    caps_written.append(cap)
            elif source and (
                "template" in source.lower() or "deterministic" in source.lower()
            ):
                if cap not in caps_templated:
                    caps_templated.append(cap)

        if payload.get("model_call"):
            model_call = detail or cap or "coder LLM call"

        done, total = payload.get("done"), payload.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            phase_done, phase_total = done, total

        lowered = detail.lower()
        if "timed out" in lowered or "timeout" in lowered:
            timeouts.append(detail[:240])

        if kind == "GATE_FAILED":
            findings = payload.get("findings") or []
            contract_misses.extend(str(f)[:240] for f in findings if f)
            if detail and detail not in contract_misses:
                contract_misses.append(detail[:240])
        if kind == "REWORK":
            rework += 1
            findings = payload.get("findings") or []
            contract_misses.extend(str(f)[:240] for f in findings if f)

    failures = dict((state or {}).get("coder_failures") or {})
    for key, reason in failures.items():
        text = str(reason)
        if "timed out" in text.lower() or "timeout" in text.lower():
            timeouts.append(f"{key}: {text[:200]}")
        if "skipped" in text.lower() or "budget" in text.lower():
            if key not in caps_templated and key not in caps_written:
                caps_templated.append(str(key))

    provenance = _provenance(workspace)
    if provenance:
        for cap in provenance.get("agent_artifacts") or []:
            if cap not in caps_written:
                caps_written.append(str(cap))
        fail_map = provenance.get("coder_failures") or {}
        for key, reason in fail_map.items():
            text = str(reason)
            if "timed out" in text.lower() and text not in timeouts:
                timeouts.append(f"{key}: {text[:200]}")

    authored = len(caps_written)
    stubbed = len(caps_templated)
    denom = authored + stubbed
    stub_rate = (stubbed / denom) if denom else 1.0 if not authored else 0.0

    pilot_ready = False
    try:
        pilot_ready = bool(ledger.pilot_ready())
    except Exception:  # noqa: BLE001 — inspect must never fail a run
        pilot_ready = False

    blockers = _pilot_ready_blockers(
        ledger, workspace, pilot_ready=pilot_ready, stub_rate=stub_rate
    )
    progressing = is_progressing(
        caps_written=caps_written,
        caps_templated=caps_templated,
        timeouts=timeouts,
        stub_rate=stub_rate,
        phase_done=phase_done,
        phase_total=phase_total,
        rework=rework,
        model_call=model_call,
    )
    snapshot: Dict[str, Any] = {
        "kind": INSPECT_NOTE_KIND,
        "current_capability": current_capability,
        "caps_written": caps_written,
        "caps_templated": caps_templated,
        "agent_written": authored,
        "templated": stubbed,
        "stub_rate": round(stub_rate, 3),
        "timeouts": timeouts[:12],
        "contract_misses": contract_misses[:12],
        "rework": rework,
        "phase_done": phase_done,
        "phase_total": phase_total,
        "model_call": model_call,
        "pilot_ready": pilot_ready,
        "pilot_ready_blockers": blockers,
        "progressing": progressing,
    }
    return snapshot


def is_progressing(
    *,
    caps_written: Sequence[str],
    caps_templated: Sequence[str],
    timeouts: Sequence[str],
    stub_rate: float,
    phase_done: int,
    phase_total: int,
    rework: int,
    model_call: str,
) -> bool:
    """True only when real manufacturing work is visible — not stubs/timeouts."""
    if caps_written:
        return True
    if phase_total > 0 and 0 < phase_done < phase_total and stub_rate < 1.0:
        return True
    if timeouts and not caps_written:
        return False
    if stub_rate >= 1.0 and not caps_written:
        return False
    if rework and not caps_written and stub_rate >= 0.9:
        return False
    if model_call and not caps_written and stub_rate >= 1.0:
        return False
    return False


def should_continue_after_inspect(snapshot: Mapping[str, Any]) -> bool:
    """Continue only when inspect shows progressing work and not pilot_ready."""
    if snapshot.get("pilot_ready"):
        return False
    return bool(snapshot.get("progressing"))


def next_stage_wall(
    elapsed_s: float,
    current_wall_s: float,
    snapshot: Mapping[str, Any],
) -> Optional[float]:
    """Return the next wall, or None when the stage must stay stopped.

    A wall already above stage 2 is honoured (no cut, no silent jump to
    the 2h ceiling). Default path: 30 min → inspect → 45 min only.
    """
    if current_wall_s <= 0:
        return None
    if current_wall_s > STAGE_2_S + 1:
        # Leftover / explicit high wall: observe only.
        return None
    if not should_continue_after_inspect(snapshot):
        return None
    if elapsed_s + 1 >= STAGE_1_S and current_wall_s <= STAGE_1_S + 1:
        return STAGE_2_S
    return None


def inspect_decision(
    *,
    elapsed_s: float,
    current_wall_s: float,
    snapshot: Mapping[str, Any],
    stage: str,
) -> Dict[str, Any]:
    """Attach a continue/stop decision to an inspect snapshot."""
    new_wall = next_stage_wall(elapsed_s, current_wall_s, snapshot)
    if new_wall:
        decision = "continue_stage_2"
        reason = (
            f"inspect {stage}: progressing "
            f"(agent_written={snapshot.get('agent_written')}, "
            f"stub_rate={snapshot.get('stub_rate')}) — bump wall "
            f"{current_wall_s:g}s → {new_wall:g}s"
        )
    elif current_wall_s > STAGE_2_S + 1:
        decision = "inspect_only_high_wall_honored"
        reason = (
            f"inspect {stage}: leftover wall {current_wall_s:g}s honoured "
            f"(no cut, no silent extra ceiling)"
        )
    elif snapshot.get("pilot_ready"):
        decision = "already_pilot_ready"
        reason = f"inspect {stage}: pilot_ready already true"
    else:
        decision = "hard_stop"
        reason = (
            f"inspect {stage}: hard-stop — "
            f"cap={snapshot.get('current_capability') or 'none'}, "
            f"written={snapshot.get('agent_written')}, "
            f"templated={snapshot.get('templated')}, "
            f"stub_rate={snapshot.get('stub_rate')}, "
            f"timeouts={len(snapshot.get('timeouts') or [])}, "
            f"contract_misses={len(snapshot.get('contract_misses') or [])}, "
            f"pilot_ready=false ({'; '.join(snapshot.get('pilot_ready_blockers') or ['unknown'])})"
        )
    out = dict(snapshot)
    out.update(
        {
            "elapsed_s": round(float(elapsed_s), 1),
            "stage": stage,
            "current_wall_s": float(current_wall_s),
            "next_wall_s": new_wall,
            "continue": new_wall is not None,
            "decision": decision,
            "reason": reason,
        }
    )
    logger.info("factory budget inspect: %s", reason)
    return out


def _provenance(workspace: Any) -> Dict[str, Any]:
    from pathlib import Path
    import json

    root = None
    if workspace is None:
        return {}
    if hasattr(workspace, "workspace"):
        root = Path(workspace.workspace)
    else:
        root = Path(workspace)
    manifest = root / "docs" / "build_provenance.json"
    if not manifest.is_file():
        return {}
    try:
        prov = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sources = prov.get("artifact_sources") or {}
    agent = sorted(
        k
        for k, v in sources.items()
        if str(v).startswith("coder LLM")
        or str(v).startswith("coder CLI")
        or "factory-grounded" in str(v).lower()
    )
    return {
        "agent_artifacts": agent,
        "coder_failures": prov.get("coder_failures") or {},
    }


def _pilot_ready_blockers(
    ledger: Any,
    workspace: Any,
    *,
    pilot_ready: bool,
    stub_rate: float,
) -> List[str]:
    blockers: List[str] = []
    if not pilot_ready:
        blockers.append("pilot_ready is false")
    if stub_rate >= 1.0:
        blockers.append("all visible artifacts are templated/stubbed")
    try:
        terminal = ledger.terminal_event()
    except Exception:  # noqa: BLE001
        terminal = None
    if terminal is None:
        blockers.append("no RUN_SUCCEEDED pilot cycle")
    else:
        payload = getattr(terminal, "payload", None) or {}
        cycle = str(payload.get("cycle") or "")
        if cycle != "pilot":
            blockers.append(f"cycle is {cycle or 'code'}, not pilot")
    # Do not call grade_workspace here — it reads build_status and would
    # recurse when status itself attaches this snapshot.
    # Deduplicate while keeping order.
    seen = set()
    out: List[str] = []
    for item in blockers:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
