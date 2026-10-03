#!/usr/bin/env python3
"""The NO HARDWIRING tripwire.

Owner's rule (2026-10-02): a failing case is never fixed by name. No per-case
branches, no literal document strings, no test-case IDs, no rescue functions,
no per-case switches. This script finds the SHAPE of a by-name fix in shipped
code and rejects any new one. It knows no case names itself -- that would be
the very thing it exists to stop.

Forms it recognises (in code only; comments and docstrings are exempt, because
a session cited in a comment is the reason a mechanism exists, while an
identifier in the path is a fixed point):

  session_id    a build-session literal (``sess_14e8b0b2``) in code
  live_snapshot a photographed product (``LIVE_VETCARE_REUSE_ACCEPT_BLOCKS``)
  rescue_fn     ``def _rescue_*``
  needle_list   ``*_NEEDLES = (...)``
  rescue_knob   ``*_RESCUE`` / ``*_BONUS`` / ``*_EXTRA_K`` knobs
  probe_id      OPT-IN (``--form probe_id``): a probe / test-case id used as
                an exact string literal (``"R18"``, ``"E1"``) -- the shape
                of a photographed probe set. Off by default here because the
                Factory's letter+digit literals are its OWN finding and stage
                codes (``"closes": "F6"``, ``item.code in {"F1", "F24"}``):
                keys in its ledger, contracts not answers. A repo whose probe
                sets are named that way (the Fork's E1/A3/R18) turns it on.

What this cannot see: a per-case branch keyed on a field NAME (``if name ==
"status"``) or an answer table (``{"database": "query"}``) has no lexical
signature. Those are caught by the classification and by the unseen-brief
gate, not by this lint. The lint is the tripwire; it is not the acceptance.

Pre-doctrine forms are grandfathered in ``scripts/hardwiring_baseline.json``.
The baseline may only shrink: ``--write-baseline`` records what is there
after a deletion batch; it is never used to admit a new form. Exit 1 is a
rejection, not a warning: if the gate fails your diff, your diff is the problem.

    python scripts/scan_hardwiring.py                 # gate (CI)
    python scripts/scan_hardwiring.py --report        # every form, by file
    python scripts/scan_hardwiring.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = ("backend/app/factory",)
BASELINE = ROOT / "scripts" / "hardwiring_baseline.json"

#: form -> pattern over a single token's text (NAME or STRING, never COMMENT).
FORMS: Dict[str, re.Pattern] = {
    "session_id": re.compile(r"sess_[0-9a-f]{6,}"),
    "live_snapshot": re.compile(r"^LIVE_[A-Z0-9_]+$"),
    "rescue_fn": re.compile(r"^_rescue_[A-Za-z0-9_]+$"),
    "needle_list": re.compile(r"^[A-Z0-9_]*_NEEDLES$"),
    "rescue_knob": re.compile(r"^[A-Z0-9_]+_(?:RESCUE|BONUS|EXTRA_K)$"),
    # A probe id is a letter and one or two digits as the WHOLE string
    # literal ("R18", "E1"): the form a comparison or a photographed set uses.
    # An id inside a longer message ("G5 stopped the run") is prose.
    "probe_id": re.compile(r"^[A-Z]-?\d{1,2}$"),
}
#: Forms matched against string literals (the literal's body, quotes and
#: prefix stripped); every other form is matched against NAME tokens.
STRING_ONLY_FORMS = {"probe_id", "session_id"}
#: Whole-body match only: the literal IS the id, not a sentence containing it.
EXACT_LITERAL_FORMS = {"probe_id"}
DEFAULT_FORMS = ("session_id", "live_snapshot", "rescue_fn", "needle_list", "rescue_knob")
OPT_IN_FORMS = ("probe_id",)


def _literal_body(text: str) -> str:
    body = re.sub(r"^[rbRBfFuU]*", "", text)
    if body[:3] in ('"""', "'''"):
        return body[3:-3]
    return body[1:-1] if len(body) >= 2 else body

Hit = Tuple[str, str, str]  # (file, form, token)


def _docstring_spans(source: str) -> List[Tuple[int, int]]:
    """Line ranges of every docstring, so a citation inside one is exempt."""
    spans: List[Tuple[int, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    return spans


def _in_spans(line: int, spans: Iterable[Tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in spans)


def scan_file(path: Path, forms: Iterable[str] = DEFAULT_FORMS) -> List[Tuple[int, str, str]]:
    """(line, form, token) for every hardwiring form in the file's CODE."""
    # utf-8-sig: a byte-order mark would make ast.parse fail, and a file whose
    # docstrings cannot be located would have every docstring citation
    # counted as code -- a false hit the file's author could not see.
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    spans = _docstring_spans(source)
    out: List[Tuple[int, str, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return out
    active = [(f, FORMS[f]) for f in forms]
    for tok in tokens:
        if tok.type not in (tokenize.NAME, tokenize.STRING):
            continue
        if _in_spans(tok.start[0], spans):
            continue
        for form, pattern in active:
            if form in STRING_ONLY_FORMS:
                if tok.type is not tokenize.STRING:
                    continue
                body = _literal_body(tok.string)
                if form in EXACT_LITERAL_FORMS:
                    if pattern.match(body):
                        out.append((tok.start[0], form, body))
                else:
                    for m in pattern.finditer(body):
                        out.append((tok.start[0], form, m.group(0)))
            elif tok.type is tokenize.NAME and pattern.match(tok.string):
                out.append((tok.start[0], form, tok.string))
    return out


def scan(roots: Iterable[str], forms: Iterable[str] = DEFAULT_FORMS) -> Dict[str, List[Tuple[int, str, str]]]:
    found: Dict[str, List[Tuple[int, str, str]]] = {}
    forms = tuple(forms)
    for root in roots:
        base = ROOT / root
        for path in sorted(base.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            hits = scan_file(path, forms)
            if hits:
                found[path.relative_to(ROOT).as_posix()] = hits
    return found


def as_counts(found: Dict[str, List[Tuple[int, str, str]]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """file -> form -> token -> count. Line numbers are deliberately dropped:
    a baseline keyed on lines would churn on every unrelated edit."""
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for file, hits in found.items():
        per_form: Dict[str, Counter] = defaultdict(Counter)
        for _line, form, token in hits:
            per_form[form][token] += 1
        out[file] = {form: dict(sorted(c.items())) for form, c in sorted(per_form.items())}
    return out


def total(counts: Dict[str, Dict[str, Dict[str, int]]]) -> int:
    return sum(n for forms in counts.values() for toks in forms.values() for n in toks.values())


def load_baseline() -> Dict[str, Dict[str, Dict[str, int]]]:
    if not BASELINE.is_file():
        return {}
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return data.get("files") or {}


def new_forms(
    current: Dict[str, Dict[str, Dict[str, int]]],
    baseline: Dict[str, Dict[str, Dict[str, int]]],
) -> List[Tuple[str, str, str, int, int]]:
    """(file, form, token, now, allowed) for every count above the baseline."""
    out = []
    for file, forms in current.items():
        for form, toks in forms.items():
            for token, n in toks.items():
                allowed = baseline.get(file, {}).get(form, {}).get(token, 0)
                if n > allowed:
                    out.append((file, form, token, n, allowed))
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", help="directory to scan (repeatable)")
    ap.add_argument(
        "--form",
        action="append",
        choices=sorted(FORMS),
        help="add an opt-in form (repeatable); default: " + ", ".join(DEFAULT_FORMS),
    )
    ap.add_argument("--report", action="store_true", help="print every form by file and exit 0")
    ap.add_argument("--write-baseline", action="store_true", help="record the current forms (after a deletion batch)")
    args = ap.parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    forms = tuple(dict.fromkeys(DEFAULT_FORMS + tuple(args.form or ())))

    found = scan(roots, forms)
    current = as_counts(found)
    now = total(current)

    if args.report:
        for file, hits in found.items():
            print(file)
            for line, form, token in hits:
                print(f"  {line:5d}  {form:14s} {token}")
        print(f"\n{now} hardwiring form(s) in shipped code under {', '.join(roots)}")
        return 0

    baseline = load_baseline()
    before = total(baseline)

    if args.write_baseline:
        if baseline and now > before:
            print(
                f"REFUSED: the baseline may only shrink ({before} -> {now}). "
                "Delete the new form; never admit it.",
                file=sys.stderr,
            )
            return 1
        BASELINE.write_text(
            json.dumps(
                {"schema": "hardwiring_baseline.v1", "roots": list(roots), "forms": list(forms), "total": now, "files": current},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"baseline written: {before} -> {now}")
        return 0

    added = new_forms(current, baseline)
    if added:
        print("REJECTED: new hardwiring form(s) in shipped code -- fix the mechanism, not the case:", file=sys.stderr)
        for file, form, token, n, allowed in added:
            print(f"  {file}: {form} {token!r} x{n} (baseline allows {allowed})", file=sys.stderr)
        print(
            f"\n{len(added)} new form(s). The baseline ({before}) may only shrink; "
            "it is never regenerated to admit a new form.",
            file=sys.stderr,
        )
        return 1
    print(f"hardwiring gate: no new form. shipped-code forms {now} (baseline {before}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
