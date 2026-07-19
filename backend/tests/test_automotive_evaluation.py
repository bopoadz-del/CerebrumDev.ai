"""Tests for automotive golden evaluation metrics."""

from __future__ import annotations

from pathlib import Path

from app.core.automotive_evaluation import load_golden_questions, run_automotive_evaluation


def test_golden_questions_pack_has_fifty_items():
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "platform_generator"
        / "overlays"
        / "automotive_core"
        / "evaluation"
        / "golden_questions.jsonl"
    )
    questions = load_golden_questions(path)
    assert len(questions) == 50


def test_evaluation_passes_with_perfect_retrieve_fn(tmp_path: Path):
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        "\n".join(
            [
                '{"question_id":"c1","category":"exact_campaign","query":"15V176000","expected_references":["15V176000"],"require_citation":true,"answerable":true}',
                '{"question_id":"i1","category":"exact_investigation","query":"PE16-007","expected_references":["PE16-007"],"require_citation":true,"answerable":true}',
                '{"question_id":"g1","category":"vehicle","query":"honda","expected_references":[],"require_citation":true,"answerable":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def retrieve(query: str, top_k: int):
        if "15V176000" in query:
            return [type("E", (), {"record_reference": "15V176000"})()]
        if "PE16-007" in query:
            return [type("E", (), {"record_reference": "PE16-007"})()]
        return [type("E", (), {"record_reference": "ok"})()]

    report = run_automotive_evaluation(golden, top_k=5, retrieve_fn=retrieve)
    assert report["passed"] is True
    assert report["metrics"]["exact_campaign_recall"] == 1.0
    assert report["metrics"]["exact_investigation_recall"] == 1.0
