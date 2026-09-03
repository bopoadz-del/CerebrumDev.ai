"""Level grade — founding-customer-ready is earned, never implied.

The three-gate verdict (CODE / PRODUCT / STORE) says what ran. This grade
says whether the artifact is a full exportable pilot or a thin scaffold.

Fail-closed: ``pilot_ready`` false cannot become STORE_GREEN or
FOUNDING_CUSTOMER_READY. Missing 14-class files, HTTP store callbacks, or
absent payload-contract helpers also block founding.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES, present_classes
from app.factory.build.product_gate import GATE_SCOPES

GRADE_EMITTER = "app.factory.build.level_grade.grade_workspace"

#: Files a founding-customer-ready export must carry in addition to the
#: 14-class contract. Absence is a blocker, not a warning.
FOUNDING_EXTRA_FILES: Sequence[str] = (
    "Dockerfile",
    "README.md",
    "requirements.txt",
    "app/block_inputs.py",
    "tests/test_routes.py",
    "frontend/src/App.tsx",
)


class Level(str, Enum):
    SCAFFOLD = "SCAFFOLD"
    CODE_GREEN = "CODE_GREEN"
    STORE_GREEN = "STORE_GREEN"
    FOUNDING_CUSTOMER_READY = "FOUNDING_CUSTOMER_READY"


_GATE_RE = re.compile(
    r"\b(CODE|PRODUCT|STORE)\s+(PASS|NOT RUN|FAIL)\b",
    re.IGNORECASE,
)


def parse_three_gate_verdict(detail: str) -> Dict[str, str]:
    """Read CODE / PRODUCT / STORE from the runner SUCCESS/FAIL sentence."""
    found = {name: "UNKNOWN" for name in GATE_SCOPES}
    for match in _GATE_RE.finditer(detail or ""):
        found[match.group(1).upper()] = match.group(2).upper().replace(" ", "_")
    return found


def _handlers_call_the_store(root: Path) -> List[str]:
    actions = root / "app" / "actions"
    if not actions.is_dir():
        return ["app/actions is missing"]
    hits: List[str] = []
    for path in sorted(actions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "httpx" in source or "/v1/execute" in source:
            hits.append(path.name)
    return hits


def _missing_founding_files(root: Path) -> List[str]:
    missing = [rel for rel in FOUNDING_EXTRA_FILES if not (root / rel).is_file()]
    classes = present_classes(root)
    missing.extend(rel for rel, ok in classes.items() if not ok)
    return missing


def _accept_payload_test_present(root: Path) -> bool:
    routes = root / "tests" / "test_routes.py"
    if not routes.is_file():
        return False
    return "def test_every_capability_route_accepts_payload" in routes.read_text(
        encoding="utf-8"
    )


def grade_workspace(
    root: Path | str,
    *,
    status: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Grade a built product tree. Never upgrades a false ``pilot_ready``.

    ``status`` is the Floor ``build_status`` dict when the caller already
    has it; otherwise the ledger is read. The grade does not re-run gates.
    """
    workspace = Path(root)
    if status is None:
        from app.factory.build_jobs import build_status

        if (workspace / "build_ledger.jsonl").is_file():
            status = build_status(workspace)
        else:
            status = {"state": "unknown", "pilot_ready": False, "detail": ""}

    state = str(status.get("state") or "unknown")
    ready = bool(status.get("pilot_ready"))
    cycle = str(status.get("cycle") or "code")
    detail = str(status.get("detail") or "")
    gates = parse_three_gate_verdict(detail)
    missing = _missing_founding_files(workspace) if workspace.is_dir() else list(
        FOUNDING_EXTRA_FILES
    ) + list(FOURTEEN_ARTIFACT_CLASSES)
    store_hits = _handlers_call_the_store(workspace) if workspace.is_dir() else [
        "app/actions is missing"
    ]
    accept = _accept_payload_test_present(workspace) if workspace.is_dir() else False

    blockers: List[str] = []
    if state != "succeeded":
        blockers.append(f"build state is {state}, not succeeded")
    if not ready:
        blockers.append("pilot_ready is false")
    if gates.get("CODE") != "PASS":
        blockers.append(f"CODE gate is {gates.get('CODE')}")
    if gates.get("PRODUCT") != "PASS":
        blockers.append(f"PRODUCT gate is {gates.get('PRODUCT')}")
    if gates.get("STORE") != "PASS":
        blockers.append(f"STORE gate is {gates.get('STORE')}")
    if missing:
        blockers.append("missing founding files: " + ", ".join(missing[:8]))
    if store_hits:
        blockers.append("handlers call the store over HTTP: " + ", ".join(store_hits[:6]))
    if not accept:
        blockers.append("tests/test_routes.py has no accept-payload contract")

    if ready and not blockers:
        level = Level.FOUNDING_CUSTOMER_READY
    elif ready and gates.get("PRODUCT") == "PASS" and gates.get("STORE") == "PASS":
        level = Level.STORE_GREEN
    elif (
        state == "succeeded"
        and cycle == "code"
        and gates.get("CODE") == "PASS"
        and not ready
    ):
        level = Level.CODE_GREEN
    else:
        level = Level.SCAFFOLD

    # Honesty lock: a false pilot_ready can never read as Store-green.
    if not ready and level in {Level.STORE_GREEN, Level.FOUNDING_CUSTOMER_READY}:
        level = Level.CODE_GREEN if gates.get("CODE") == "PASS" else Level.SCAFFOLD
        blockers.append("honesty lock: pilot_ready false cannot grade Store-green")

    return {
        "emitter": GRADE_EMITTER,
        "level": level.value,
        "pilot_ready": ready,
        "cycle": cycle,
        "state": state,
        "three_gate": gates,
        "gate_scopes": dict(GATE_SCOPES),
        "missing": missing,
        "blockers": blockers,
        "founding_customer_ready": level is Level.FOUNDING_CUSTOMER_READY,
    }


def attach_level_grade(status: Dict[str, Any], root: Path | str) -> Dict[str, Any]:
    """Stamp ``level_grade`` onto a build_status dict. Fail-closed on error."""
    try:
        status["level_grade"] = grade_workspace(root, status=status)
    except Exception as exc:  # noqa: BLE001 — a grade fault must not 500 status
        status["level_grade"] = {
            "emitter": GRADE_EMITTER,
            "level": Level.SCAFFOLD.value,
            "pilot_ready": False,
            "founding_customer_ready": False,
            "blockers": [f"level grade could not run: {type(exc).__name__}: {exc}"],
        }
    return status
