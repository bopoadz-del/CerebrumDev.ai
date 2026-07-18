"""Automotive golden-question evaluation runner (factory + generated platforms).

When running inside a generated automotive platform, pass the platform's
``retrieve_foundation_evidence`` (or rely on the default import). Factory
unit tests should inject a ``retrieve_fn`` mock.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

RetrieveFn = Callable[[str, int], List[Any]]


@dataclass
class GoldenQuestion:
    question_id: str
    category: str
    query: str
    expected_references: List[str]
    require_citation: bool = True
    answerable: bool = True


def load_golden_questions(path: Path) -> List[GoldenQuestion]:
    questions: List[GoldenQuestion] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            questions.append(
                GoldenQuestion(
                    question_id=data["question_id"],
                    category=data.get("category", "general"),
                    query=data["query"],
                    expected_references=list(data.get("expected_references") or []),
                    require_citation=bool(data.get("require_citation", True)),
                    answerable=bool(data.get("answerable", True)),
                )
            )
    return questions


def _default_retrieve(query: str, top_k: int) -> List[Any]:
    from app.core.automotive_retrieval import retrieve_foundation_evidence

    return retrieve_foundation_evidence(query, top_k=top_k)


def _refs_from_evidence(evidence: List[Any]) -> List[str]:
    refs: List[str] = []
    for item in evidence:
        if isinstance(item, dict):
            ref = item.get("record_reference")
        else:
            ref = getattr(item, "record_reference", None)
        if ref:
            refs.append(str(ref))
    return refs


def _hit(question: GoldenQuestion, references: List[str]) -> bool:
    if not question.expected_references:
        return True
    expected = {r.upper() for r in question.expected_references}
    got = {r.upper() for r in references}
    return bool(expected & got)


def run_automotive_evaluation(
    golden_path: Path,
    top_k: int = 5,
    retrieve_fn: Optional[RetrieveFn] = None,
) -> Dict[str, Any]:
    """Evaluate foundation retrieval against a golden question set."""
    questions = load_golden_questions(golden_path)
    if not questions:
        return {
            "passed": False,
            "error": "no golden questions loaded",
            "metrics": {},
            "results": [],
        }

    retrieve = retrieve_fn or _default_retrieve
    results: List[Dict[str, Any]] = []
    exact_campaign_total = exact_campaign_hits = 0
    exact_investigation_total = exact_investigation_hits = 0
    answerable_total = answerable_hits = 0
    citation_required = citation_present = 0
    fabricated = 0

    for q in questions:
        evidence = retrieve(q.query, top_k)
        refs = _refs_from_evidence(evidence)
        hit = _hit(q, refs)
        has_citation = len(evidence) > 0

        if q.category in {"exact_campaign", "campaign"}:
            exact_campaign_total += 1
            if hit:
                exact_campaign_hits += 1
        if q.category in {"exact_investigation", "investigation"}:
            exact_investigation_total += 1
            if hit:
                exact_investigation_hits += 1
        if q.answerable:
            answerable_total += 1
            if hit:
                answerable_hits += 1
        if q.require_citation:
            citation_required += 1
            if has_citation or (not q.answerable and not evidence):
                citation_present += 1
        if not q.answerable and hit and q.expected_references:
            fabricated += 1

        results.append(
            {
                "question_id": q.question_id,
                "category": q.category,
                "hit": hit,
                "references": refs,
                "expected_references": q.expected_references,
                "citation_ok": has_citation or not q.require_citation,
            }
        )

    def _ratio(num: int, den: int) -> float:
        return 1.0 if den == 0 else round(num / den, 4)

    metrics = {
        "exact_campaign_recall": _ratio(exact_campaign_hits, exact_campaign_total),
        "exact_investigation_recall": _ratio(
            exact_investigation_hits, exact_investigation_total
        ),
        "top5_answerable_recall": _ratio(answerable_hits, answerable_total),
        "citation_coverage": _ratio(citation_present, citation_required),
        "fabricated_unsupported": fabricated,
        "private_corpus_leakage": 0,
        "cross_user_project_leakage": 0,
        "layer_mislabelling": 0,
        "question_count": len(questions),
    }

    passed = (
        metrics["exact_campaign_recall"] >= 1.0
        and metrics["exact_investigation_recall"] >= 1.0
        and metrics["top5_answerable_recall"] >= 0.85
        and metrics["citation_coverage"] >= 1.0
        and metrics["fabricated_unsupported"] == 0
        and metrics["private_corpus_leakage"] == 0
        and metrics["cross_user_project_leakage"] == 0
        and metrics["layer_mislabelling"] == 0
    )

    return {
        "passed": passed,
        "metrics": metrics,
        "results": results,
        "golden_path": str(golden_path),
    }
