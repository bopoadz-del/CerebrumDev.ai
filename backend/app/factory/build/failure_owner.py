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
        text = str(line)
        m = _FAILED_LINE.match(text)
        if m:
            name = (m.group(2) or "").split("::")[-1]
            # Keep the "- <ExcType>: ..." tail: it is the only signal in the
            # fallback path of whether the product failed an assertion (its
            # fault) or the test code itself broke (the factory's).
            _, _, tail = text.partition(" - ")
            out.append({"file": m.group(1), "nodeid": m.group(1) + ("::" + m.group(2) if m.group(2) else ""),
                        "name": name, "innermost": "", "message": tail.strip(), "text": text})
    return out


def _is_product_failure(row: Dict[str, str]) -> bool:
    """The product, not the test code, is why this row is red.

    Two ways that happens, and both mean a WRITER rework can fix it even though
    the failing test lives in a file the writer may not edit:

    * the traceback ends inside ``app/**`` -- the product raised; or
    * the test's own assertion fired -- the factory built a valid input and the
      product returned something the assertion rejected (``open_items`` read
      back as a string). The factory's generated assertions compare product
      input to product output, so a failing one is a product defect, not a
      wrong expectation.

    A non-assertion error in the test (``KeyError``/``ImportError``/collection)
    is the test code or its mined contract breaking -- that stays the factory's.
    """
    innermost = str(row.get("innermost") or "")
    if innermost.startswith("app/") or "/app/" in innermost:
        return True
    blob = " ".join(str(row.get(k) or "") for k in ("message", "text"))
    if "AssertionError" not in blob:
        return False
    # An AssertionError is the product's doing only when the assertion COMPARED
    # product output to an expected value -- the generated round-trip and route
    # checks (``assert fetched[key] == value, (key, fetched[key], value)``).
    # Their failure carries the comparison: a ``==``/``!=`` in the asserted
    # source line, or a ``(key, got, want)`` tuple in the message. A stub the
    # factory itself wrote broken (``assert False, 'broken on purpose'``)
    # carries neither -- that is a broken test, the factory's, not the
    # product's, and it must halt with no rework (test_g_series_rework_loop).
    message = str(row.get("message") or "")
    if re.search(r"\([^()]*,[^()]*\)", message):  # a (key, got, want) tuple
        return True
    return bool(re.search(r"assert\b[^\n]*[=!]=", blob))  # a compared assertion


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
    product_owned, factory_owned = [], []
    for row in failing:
        rel = str(row.get("file") or "").replace("\\", "/")
        # A row outside the factory's own test files is product-owned by
        # definition. A row inside them is the factory's ONLY when the test
        # code itself broke, not when the product failed the test.
        if rel in factory_files and not _is_product_failure(row):
            factory_owned.append(row)
        else:
            product_owned.append(row)

    # Route to PRODUCT whenever anything product-owned failed: the writer can
    # fix those, and a rework round is only wasted when there is nothing for it
    # to do. A single factory-owned row must not bury a product row and halt a
    # run the writer could advance -- the pure-factory remainder (if any)
    # surfaces on the next round once the product rows are green.
    if product_owned:
        return {"owner": PRODUCT, "tests": [r.get("nodeid") or r.get("name") for r in product_owned],
                "generator": "", "reason": reason}
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
