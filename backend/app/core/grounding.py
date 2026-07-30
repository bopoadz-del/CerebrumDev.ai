"""Platform grounding stage — mandatory verdict on every produced answer.

Verdicts:
- ``grounded``          every checkable claim traces to a supplied source
- ``flag-as-estimate``  unsupported figures exist; answer is released only
                        with an explicit estimate disclosure attached
- ``blocked``           the answer invents URLs (or, in strict mode, figures);
                        the allowed response is ``None`` — never a raw
                        ungrounded fallback

Every verdict is persisted to an append-only JSONL audit log under
``STORAGE_PATH/grounding/verdicts.jsonl``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

VERDICT_GROUNDED = "grounded"
VERDICT_FLAG = "flag-as-estimate"
VERDICT_BLOCKED = "blocked"

_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
# Figures worth checking: 2+ digit numbers, decimals, or percentages.
_FIGURE_RE = re.compile(r"\b\d[\d,]*\.\d+%?|\b\d[\d,]{1,}%?")


def _normalize_figure(raw: str) -> str:
    return raw.replace(",", "").rstrip("%.")


def _figures(text: str) -> List[str]:
    return [_normalize_figure(m) for m in _FIGURE_RE.findall(text)]


def evaluate_grounding(
    answer: str,
    *,
    sources: Iterable[str],
    query: str = "",
    strict: bool = False,
) -> Dict[str, Any]:
    """Return a grounding verdict for ``answer`` against ``sources``.

    ``query`` counts as grounded context: figures the caller supplied may be
    echoed back without being flagged.
    """
    answer = answer or ""
    corpus = "\n".join([s for s in sources if s] + [query or ""])
    corpus_normalized = corpus.replace(",", "")
    reasons: List[str] = []

    invented_urls = [u for u in _URL_RE.findall(answer) if u.rstrip(".,") not in corpus]
    if invented_urls:
        reasons.append(
            "invented URL(s) not present in any source: " + ", ".join(invented_urls)
        )
        return {
            "verdict": VERDICT_BLOCKED,
            "allowed_response": None,
            "reasons": reasons,
            "unsupported_figures": [],
        }

    unsupported = [f for f in _figures(answer) if f not in corpus_normalized]
    if unsupported:
        reasons.append(
            "figure(s) not present in any source: " + ", ".join(sorted(set(unsupported)))
        )
        if strict:
            return {
                "verdict": VERDICT_BLOCKED,
                "allowed_response": None,
                "reasons": reasons,
                "unsupported_figures": sorted(set(unsupported)),
            }
        disclosure = (
            "\n\n[Estimate disclosure: the figure(s) "
            + ", ".join(sorted(set(unsupported)))
            + " could not be verified against any grounded source in this "
            "session — treat them as estimates.]"
        )
        return {
            "verdict": VERDICT_FLAG,
            "allowed_response": answer + disclosure,
            "reasons": reasons,
            "unsupported_figures": sorted(set(unsupported)),
        }

    return {
        "verdict": VERDICT_GROUNDED,
        "allowed_response": answer,
        "reasons": [],
        "unsupported_figures": [],
    }


def verdict_log_path() -> Path:
    root = Path(os.getenv("STORAGE_PATH", "./storage")) / "grounding"
    root.mkdir(parents=True, exist_ok=True)
    return root / "verdicts.jsonl"


def persist_verdict(record: Dict[str, Any], *, path: Optional[Path] = None) -> Dict[str, Any]:
    """Append one verdict record to the grounding audit log."""
    entry = dict(record)
    entry["recorded_at"] = datetime.now(timezone.utc).isoformat()
    target = path or verdict_log_path()
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    return entry


# ---------------------------------------------------------------------------
# Scope refusal — questions never to attempt, even when perfectly grounded.
# The default list covers professional-judgement categories; extend or
# replace via a JSON file at CEREBRUM_SCOPE_REFUSALS_PATH with
# [{"id": ..., "pattern": <regex>, "reason": ...}, ...].
# ---------------------------------------------------------------------------

VERDICT_OUT_OF_SCOPE = "out_of_scope"

_DEFAULT_SCOPE_REFUSALS = [
    {
        "id": "medication_dosing",
        "pattern": r"\b(dose|dosage|dosing|administer)\b.*\b(patient|medication|drug|mg|ml)\b"
        r"|\b(patient|medication|drug)\b.*\b(dose|dosage|dosing|administer)\b",
        "reason": "Medication dosing is a clinical decision; this system never answers it.",
    },
    {
        "id": "structural_signoff",
        "pattern": r"\b(certify|sign[- ]?off|approve)\b.*\b(structural|beam|column|foundation|load[- ]?bearing)\b"
        r"|\b(structural|beam|column|foundation)\b.*\b(certify|sign[- ]?off|adequate for sign)\b",
        "reason": "Structural adequacy certification requires a licensed engineer; this system never signs off.",
    },
    {
        "id": "legal_filing",
        "pattern": r"\b(file|filing|statute of limitations|court deadline)\b.*\b(lawsuit|claim|court)\b"
        r"|\bshould i (sue|plead)\b",
        "reason": "Legal filing strategy and deadlines require counsel; this system never advises on them.",
    },
    {
        "id": "life_safety_emergency",
        "pattern": r"\b(emergency|evacuat\w+|mayday|engine (failure|fire))\b.*\b(now|immediately|in[- ]?flight|right now)\b",
        "reason": "Live emergency response belongs to certified operators and official procedures, not this system.",
    },
]

_SCOPE_CACHE: Optional[list] = None


def _scope_refusals() -> list:
    global _SCOPE_CACHE
    if _SCOPE_CACHE is not None:
        return _SCOPE_CACHE
    rules = list(_DEFAULT_SCOPE_REFUSALS)
    override = os.getenv("CEREBRUM_SCOPE_REFUSALS_PATH", "").strip()
    if override:
        try:
            rules = json.loads(Path(override).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # fail closed to defaults, never to an empty list
    _SCOPE_CACHE = [
        {**r, "_compiled": re.compile(r["pattern"], re.IGNORECASE | re.DOTALL)}
        for r in rules
        if r.get("pattern")
    ]
    return _SCOPE_CACHE


def check_scope_refusal(query: str) -> Optional[Dict[str, Any]]:
    """Return the matched refusal rule for ``query``, or None.

    Checked BEFORE retrieval/answering: a matched question is never
    attempted, however grounded the corpus may be.
    """
    text = query or ""
    for rule in _scope_refusals():
        if rule["_compiled"].search(text):
            return {"id": rule["id"], "reason": rule["reason"]}
    return None
