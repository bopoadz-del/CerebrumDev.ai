"""U7: PILOT_READY requires a pilot cycle that actually ran.

``evaluate_promotion`` computed PILOT_READY from stage evidence files and
kernel ownership alone. Nothing tied it to a build. Writing three JSON files
under ``build/stages`` was therefore sufficient to flip the flag, with no
pilot cycle having executed anywhere -- which is precisely the claim the
spec forbids: *pilot_ready may only be set by a gate that observed a
business outcome, and no human may assert it*.

The evidence a pilot cycle leaves behind is its ledger. ``BuildLedger``
records ``RUN_SUCCEEDED`` with ``cycle == "pilot"`` only after all five role
gates pass on the pilot marker, which includes the STORE_MANAGER durability
gate. That event is the thing worth requiring, because it cannot be produced
by writing a document.

This module locates such a ledger and reports what it found. It never
synthesises one: an absent ledger is reported as unproven, not assumed.

Scope note: this proves *a* pilot cycle succeeded for the product built in
the given workspace. It does not prove that cycle corresponds to the source
revision under promotion -- that needs an artifact identity comparison and
is left explicit rather than implied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Env var naming the workspace whose pilot cycle backs this promotion.
WORKSPACE_ENV = "FACTORY_PILOT_WORKSPACE"

LEDGER_NAME = "build_ledger.jsonl"


def _candidate_ledgers(explicit: Optional[Path]) -> List[Path]:
    out: List[Path] = []
    if explicit is not None:
        p = Path(explicit)
        out.append(p / LEDGER_NAME if p.is_dir() else p)
    env = os.getenv(WORKSPACE_ENV)
    if env:
        p = Path(env)
        out.append(p / LEDGER_NAME if p.is_dir() else p)
    return [p for p in out if p.name == LEDGER_NAME]


def _succeeded_pilot_events(ledger_path: Path) -> List[Dict[str, Any]]:
    """RUN_SUCCEEDED events whose cycle is pilot."""
    found: List[Dict[str, Any]] = []
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("kind")) != "RUN_SUCCEEDED":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("cycle") == "pilot" or payload.get("pilot_ready") is True:
            found.append(event)
    return found


def inspect_pilot_cycle(workspace: Optional[Path] = None) -> Dict[str, Any]:
    """Did a pilot cycle actually succeed? Reports, never assumes."""
    result: Dict[str, Any] = {
        "ok": False,
        "reason": None,
        "ledger": None,
        "succeeded_pilot_runs": 0,
        "workspace_env": WORKSPACE_ENV,
        "note": (
            "proves a pilot cycle succeeded for this workspace; does not prove "
            "it matches the revision under promotion"
        ),
    }
    candidates = _candidate_ledgers(workspace)
    if not candidates:
        result["reason"] = (
            f"no pilot workspace given — set {WORKSPACE_ENV} to the build "
            "workspace whose pilot cycle backs this promotion"
        )
        return result

    for path in candidates:
        if not path.is_file():
            continue
        result["ledger"] = str(path)
        events = _succeeded_pilot_events(path)
        result["succeeded_pilot_runs"] = len(events)
        if events:
            result["ok"] = True
            return result
        result["reason"] = "ledger has no RUN_SUCCEEDED event with cycle=pilot"
        return result

    result["reason"] = f"ledger not found at {candidates[0]}"
    return result
