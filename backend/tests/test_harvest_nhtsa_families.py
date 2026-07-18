"""Complaint and safety-rating harvest regression tests."""

from __future__ import annotations

from pathlib import Path

from scripts.harvest_nhtsa import harvest_complaints, harvest_safety_ratings


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_harvest_complaints_from_fixture(tmp_path: Path):
    result = harvest_complaints(
        output_dir=tmp_path,
        fixture_path=FIXTURES / "nhtsa_complaints_sample.txt",
    )
    assert result["record_count"] == 3
    assert result["harvest_manifest"]["source_family"] == "complaint"
    canonical = Path(result["canonical_path"])
    assert canonical.exists()
    lines = canonical.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert '"source_family":"complaint"' in lines[0]


def test_harvest_safety_ratings_from_fixture(tmp_path: Path):
    result = harvest_safety_ratings(
        output_dir=tmp_path,
        fixture_path=FIXTURES / "nhtsa_safety_ratings_sample.txt",
    )
    assert result["record_count"] == 3
    assert result["harvest_manifest"]["source_family"] == "safety_rating"
    canonical = Path(result["canonical_path"])
    lines = canonical.read_text(encoding="utf-8").strip().splitlines()
    assert '"source_family":"safety_rating"' in lines[0]
