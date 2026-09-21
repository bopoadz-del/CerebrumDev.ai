"""Who owns a red TESTER suite -- and so whether a rework round can fix it.

G1. The runner used to send EVERY TESTER failure back to the WRITER. But the
WRITER is forbidden to edit tests/ (it may not author the tests that judge
it), so a failure inside a test the Factory itself generated could never be
fixed by rework: each round was a guaranteed-wasted writer pass, and a run
burned its whole budget -- then a resume burned another one -- on a defect
only a Factory change could clear. Live: four resumes of one voice-agent
build, each ending on a Factory-generated test.

Rule:
  * ENVIRONMENT -- the suite could not run (missing dependency, no pytest).
  * FACTORY     -- a failing test lives in a file TESTER wrote this run, and
                   it broke in the test itself, not in product code.
  * PRODUCT     -- everything else, including a Factory-written test whose
                   traceback ends inside ``app/**``: the product raised, so
                   the writer can fix it.
Only PRODUCT dispatches a WRITER rework.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FACTORY = "FACTORY"
ENVIRONMENT = "ENVIRONMENT"
PRODUCT = "PRODUCT"

_BUILD_DIR = Path(__file__).resolve().parent
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?\.py)(?:::(\S+))?")


def _failing(verdict: Any) -> List[Dict[str, str]]:
    payload = getattr(verdict, "payload", None) or {}
    rows = payload.get("failing_tests")
    if rows:
        return [dict(r) for r in rows]
    out = []
    for line in getattr(verdict, "findings", None) or []:
        m = _FAILED_LINE.match(str(line))
        if m:
            name = (m.group(2) or "").split("::")[-1]
            out.append({"file": m.group(1), "nodeid": m.group(1) + ("::" + m.group(2) if m.group(2) else ""),
                        "name": name, "innermost": ""})
    return out


def generator_location(test_name: str, test_file: str) -> str:
    """``factory/build/<module>.py:<line>`` that emits this test, best effort."""
    base = Path(test_file or "").name
    candidates = sorted(_BUILD_DIR.glob("*.py"))
    needles = []
    if test_name:
        needles.append(f"def {test_name}(")
    if base:
        needles += [f'"{base}"', f"'{base}'"]
    for needle in needles:
        for path in candidates:
            try:
                for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if needle in line:
                        return f"app/factory/build/{path.name}:{no}"
            except OSError:
                continue
    return f"emitted by TESTER into {test_file or 'tests/'}"


def classify(verdict: Any, factory_test_files: Iterable[str]) -> Dict[str, Any]:
    """``{owner, tests, generator, reason}`` for a failed TESTER verdict."""
    payload = getattr(verdict, "payload", None) or {}
    reason = str(getattr(verdict, "reason", "") or "")
    if reason in ("environment_fault", "suite_could_not_run") or payload.get("infrastructure"):
        return {"owner": ENVIRONMENT, "tests": [], "generator": "", "reason": reason}

    factory_files = {str(f).replace("\\", "/") for f in (factory_test_files or ())}
    failing = _failing(verdict)
    factory_owned = []
    for row in failing:
        rel = str(row.get("file") or "").replace("\\", "/")
        innermost = str(row.get("innermost") or "")
        product_raised = innermost.startswith("app/") or "/app/" in innermost
        if rel in factory_files and not product_raised:
            factory_owned.append(row)
    if factory_owned:
        first = factory_owned[0]
        return {
            "owner": FACTORY,
            "tests": [r.get("nodeid") or r.get("name") for r in factory_owned],
            "generator": generator_location(str(first.get("name") or ""), str(first.get("file") or "")),
            "reason": reason,
        }
    return {"owner": PRODUCT, "tests": [r.get("nodeid") or r.get("name") for r in failing],
            "generator": "", "reason": reason}


def failure_names(verdict: Any) -> List[str]:
    """Stable names for G5's same-failure-twice rule."""
    names = [r.get("nodeid") or r.get("name") for r in _failing(verdict)]
    names = [n for n in names if n]
    if names:
        return sorted(set(names))
    gate = str(getattr(verdict, "gate", "") or "")
    return [f"{gate}:{getattr(verdict, 'reason', '') or 'failed'}"] if gate else []


def repeated(previous: Optional[Iterable[str]], current: Iterable[str]) -> List[str]:
    """Names that failed in the previous round AND fail again now."""
    return sorted(set(previous or ()) & set(current or ()))
