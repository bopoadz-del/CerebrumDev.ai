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
            # pytest prints ERROR for a collection/setup error and FAILED for a
            # test that ran and failed. That distinction is the failure SHAPE
            # G5 must not collapse (D3): a stripped stub that errors at import
            # and a real assertion carry the same nodeid but are not the same
            # failure.
            kind = "error" if text.lstrip().upper().startswith("ERROR") else "failure"
            out.append({"file": m.group(1), "nodeid": m.group(1) + ("::" + m.group(2) if m.group(2) else ""),
                        "name": name, "innermost": "", "message": tail.strip(), "text": text,
                        "kind": kind})
    return out


def _row_kind(row: Dict[str, str]) -> str:
    """The failure shape: 'error' (collection/setup/raise) or 'failure'
    (a test that ran and asserted). Defaults to 'failure' when a row carries
    no kind (older ledgers), which keeps the pre-D3 behaviour for those."""
    kind = str(row.get("kind") or "").strip().lower()
    return kind if kind in ("error", "failure") else "failure"


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


def classify(
    verdict: Any,
    factory_test_files: Iterable[str],
    behavior_test_files: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """``{owner, tests, generator, reason}`` for a failed TESTER verdict.

    ``behavior_test_files`` -- the tests run_tester's own emitters stamped --
    makes ownership constructional: an AssertionError in one of THOSE files
    is the product failing a behavior check by how the suite is built (its
    assertions compare product input to product output, or demand a refusal
    the product owes), so no message-shape sniffing is needed. A file that is
    in ``factory_test_files`` (written during the TESTER phase, writer may
    not edit it) but NOT in the behavior list was injected -- a broken stub's
    assertion is nobody's product. Without the behavior list (older ledgers,
    resumes of pre-upgrade runs) the heuristic fallback below still applies.
    """
    payload = getattr(verdict, "payload", None) or {}
    reason = str(getattr(verdict, "reason", "") or "")
    if reason in ("environment_fault", "suite_could_not_run") or payload.get("infrastructure"):
        return {"owner": ENVIRONMENT, "tests": [], "generator": "", "reason": reason}

    factory_files = {str(f).replace("\\", "/") for f in (factory_test_files or ())}
    behavior = {str(f).replace("\\", "/") for f in (behavior_test_files or ())}
    failing = _failing(verdict)
    product_owned, factory_owned = [], []
    for row in failing:
        rel = str(row.get("file") or "").replace("\\", "/")
        # A row outside the factory's own test files is product-owned by
        # definition. A row inside them is the factory's ONLY when the test
        # code itself broke, not when the product failed the test.
        if behavior:
            innermost = str(row.get("innermost") or "")
            blob = " ".join(str(row.get(k) or "") for k in ("message", "text", "nodeid"))
            is_product = (
                innermost.startswith("app/")
                or "/app/" in innermost
                or (rel in behavior and "AssertionError" in blob)
            )
        else:
            is_product = _is_product_failure(row)
        if rel in factory_files and not is_product:
            factory_owned.append(row)
        else:
            product_owned.append(row)

    # Route to PRODUCT whenever anything product-owned failed: the writer can
    # fix those, and a rework round is only wasted when there is nothing for it
    # to do. A single factory-owned row must not bury a product row and halt a
    # run the writer could advance -- the pure-factory remainder (if any)
    # surfaces on the next round once the product rows are green.
    if product_owned:
        # D4: a factory-owned row must not vanish just because a product row
        # routed the verdict PRODUCT. The writer is not asked to fix it (the
        # tests stay PRODUCT-routed), but the owner needs to see the factory
        # still owes a fix, so the caller can name it in the note and halt.
        return {"owner": PRODUCT, "tests": [r.get("nodeid") or r.get("name") for r in product_owned],
                "generator": "", "reason": reason,
                "factory_owned": [r.get("nodeid") or r.get("name") for r in factory_owned]}
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
    """Stable keys for G5's same-failure-twice rule, carrying the failure SHAPE.

    D3 (live 2026-09-29): G5 keyed on the nodeid alone, so a test that failed
    round 1 by NOT importing (a collection error) and round 2 by ASSERTING
    counted as "the same failure twice" and the run was killed -- even though
    the second round was a different, progressing defect. The key now embeds
    the row's kind (``<nodeid> [error]`` / ``[failure]``), so the two are
    distinct. Rows from an older ledger carry no kind and default to
    ``failure``: they simply will not match a new ``[error]`` key, which costs
    at most one extra rework round -- never a false halt.
    """
    keys = []
    for row in _failing(verdict):
        nodeid = row.get("nodeid") or row.get("name")
        if nodeid:
            keys.append(f"{nodeid} [{_row_kind(row)}]")
    if keys:
        return sorted(set(keys))
    gate = str(getattr(verdict, "gate", "") or "")
    return [f"{gate}:{getattr(verdict, 'reason', '') or 'failed'}"] if gate else []


def repeated(previous: Optional[Iterable[str]], current: Iterable[str]) -> List[str]:
    """Names that failed in the previous round AND fail again now."""
    return sorted(set(previous or ()) & set(current or ()))
