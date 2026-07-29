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
