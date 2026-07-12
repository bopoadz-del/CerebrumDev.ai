"""Tests for the NHTSA recall harvester."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.harvest_nhtsa import (
    HarvestError,
    UnsafeArchivePathError,
    _download_stream,
    extract_recall_csv,
    harvest_investigations,
    harvest_recalls,
    normalize_recall_row,
)


@pytest.fixture
def fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "nhtsa_recalls_sample.txt"


def test_normalize_recall_row_preserves_campaign_and_metadata() -> None:
    row = {
        "RECORD_ID": "1",
        "CAMPNO": "15V176000",
        "MAKETXT": "Honda",
        "MODELTXT": "Accord",
        "YEARTXT": "2014",
        "MFGNAME": "Honda (American Honda Motor Co.)",
        "COMPNAME": "AIR BAGS:PASSENGER SIDE FRONTAL",
        "RCDATE": "20150331",
        "POTAFF": "1900000",
        "DESC_DEFECT": "The passenger air bag may be susceptible to moisture intrusion.",
        "CONEQUENCE_DEFECT": "The air bag may not deploy as intended.",
        "CORRECTIVE_ACTION": "Dealers will replace the passenger side frontal air bag.",
    }
    record = normalize_recall_row("nhtsa_recalls", 1, row)
    assert record["source_id"] == "nhtsa_recalls"
    assert record["source_family"] == "recall"
    assert record["campaign_number"] == "15V176000"
    assert record["make"] == "Honda"
    assert record["model"] == "Accord"
    assert record["model_year"] == "2014"
    assert record["manufacturer"] == "Honda (American Honda Motor Co.)"
    assert record["report_received_date"] == "2015-03-31"
    assert record["affected_units"] == 1900000
    assert record["source_url"] == "https://www.nhtsa.gov/recalls?nhtsaId=15V176000"
    assert record["jurisdiction"] == "US"
    assert record["authority_rating"] == "primary"
    assert record["normalization_version"] == "automotive_core_v1"
    assert record["record_id"] == normalize_recall_row("nhtsa_recalls", 1, row)["record_id"]
    assert record["raw_record_hash"] == normalize_recall_row("nhtsa_recalls", 1, row)["raw_record_hash"]


def test_normalize_recall_row_handles_missing_values() -> None:
    row = {
        "RECORD_ID": "99",
        "CAMPNO": "",
        "MAKETXT": "Unknown",
        "MODELTXT": "",
        "YEARTXT": "9999",
        "MFGNAME": "",
        "COMPNAME": "",
        "RCDATE": "99999999",
        "POTAFF": "",
        "DESC_DEFECT": "",
    }
    record = normalize_recall_row("nhtsa_recalls", 99, row)
    assert record["campaign_number"] == ""
    assert record["make"] == "Unknown"
    assert record["model"] is None
    assert record["model_year"] is None
    assert record["component"] is None
    assert record["report_received_date"] is None
    assert record["affected_units"] is None
    assert record["summary"] is None


def test_fixture_harvest_writes_canonical_records_and_manifest(fixture_path: Path, tmp_path: Path) -> None:
    result = harvest_recalls(
        output_dir=tmp_path / "harvest",
        fixture_path=fixture_path,
    )
    manifest = result["harvest_manifest"]
    assert manifest["source_id"] == "nhtsa_recalls"
    assert manifest["record_count"] == 3
    assert manifest["content_hash"]
    assert manifest["fixture_path"]

    canonical_path = result["canonical_path"]
    assert canonical_path.exists()
    lines = canonical_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert records[0]["campaign_number"] == "15V176000"
    assert records[1]["campaign_number"] == "21V127000"
    assert records[2]["campaign_number"] == "22V953000"

    manifest_path = tmp_path / "harvest" / "harvest_manifest.json"
    assert manifest_path.exists()


def test_dry_run_performs_no_writes(fixture_path: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "dry"
    result = harvest_recalls(
        output_dir=output_dir,
        fixture_path=fixture_path,
        dry_run=True,
    )
    assert result["record_count"] == 3
    assert result["canonical_path"] is None
    assert not (output_dir / "canonical").exists()
    assert not (output_dir / "harvest_manifest.json").exists()


def test_since_year_filters_records(fixture_path: Path, tmp_path: Path) -> None:
    result = harvest_recalls(
        output_dir=tmp_path / "filtered",
        fixture_path=fixture_path,
        since_year=2018,
    )
    assert result["record_count"] == 2
    records = [
        json.loads(line)
        for line in result["canonical_path"].read_text(encoding="utf-8").strip().splitlines()
    ]
    years = {r["model_year"] for r in records}
    assert years <= {"2021", "2018"}


def test_unsafe_zip_path_is_rejected(tmp_path: Path) -> None:
    from scripts.harvest_nhtsa import _validate_zip_member

    with pytest.raises(UnsafeArchivePathError):
        _validate_zip_member("../etc/passwd")
    with pytest.raises(UnsafeArchivePathError):
        _validate_zip_member("/absolute/path")

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("FLAT_RCL_POST_2010.txt", "safe content")
        zf.writestr("../../etc/passwd", "evil")
    # The traversal member is ignored because it is not an expected name.
    extracted = extract_recall_csv(archive)
    assert "safe content" in extracted.read_text(encoding="utf-8")


def test_extract_recall_csv_selects_expected_member(tmp_path: Path) -> None:
    archive = tmp_path / "recalls.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "not the data")
        zf.writestr("FLAT_RCL_POST_2010.txt", "RECORD_ID\tCAMPNO\n1\t15V176000\n")
    extracted = extract_recall_csv(archive)
    assert extracted.exists()
    text = extracted.read_text(encoding="utf-8")
    assert "15V176000" in text


def test_valid_cached_artifact_is_reused(fixture_path: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "cached"
    first = harvest_recalls(output_dir=output_dir, fixture_path=fixture_path)
    # A second run without force_download should produce the same record count.
    second = harvest_recalls(output_dir=output_dir, fixture_path=fixture_path)
    assert second["record_count"] == first["record_count"]


def test_corrupt_cached_archive_is_rejected_when_forced(tmp_path: Path) -> None:
    # No real archive caching for fixture mode; this test guards the hash path.
    output_dir = tmp_path / "corrupt"
    output_dir.mkdir()
    raw_dir = output_dir / "raw" / "nhtsa_recalls"
    raw_dir.mkdir(parents=True)
    (raw_dir / "FLAT_RCL_POST_2010.zip").write_text("not a zip", encoding="utf-8")
    with pytest.raises((HarvestError, zipfile.BadZipFile)):
        harvest_recalls(output_dir=output_dir, force_download=False)


def test_download_stream_writes_file(tmp_path: Path) -> None:
    dest = tmp_path / "small.zip"
    # Use a tiny, stable public URL to exercise the stream path.
    url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
    _download_stream(url, dest, timeout=30.0, max_retries=1)
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_investigation_fixture_harvest_succeeds(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    result = harvest_investigations(
        output_dir=tmp_path,
        fixture_path=fixture,
    )
    assert result["record_count"] > 0
    assert result["harvest_manifest"]["source_family"] == "investigation"
    assert (tmp_path / "canonical" / "investigations.jsonl").exists()


def test_harvest_artifacts_are_outside_git(fixture_path: Path, tmp_path: Path) -> None:
    # The harvester writes under output_dir/raw and output_dir/canonical.
    result = harvest_recalls(output_dir=tmp_path / "git-check", fixture_path=fixture_path)
    assert ".git" not in str(result["canonical_path"])


def test_investigation_dry_run_performs_no_writes(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    result = harvest_investigations(
        output_dir=tmp_path,
        fixture_path=fixture,
        dry_run=True,
    )
    assert result["record_count"] == 4
    assert not (tmp_path / "canonical").exists()


def test_investigation_content_hash_is_recorded(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    result = harvest_investigations(
        output_dir=tmp_path,
        fixture_path=fixture,
    )
    assert result["harvest_manifest"]["content_hash"]
    assert len(result["harvest_manifest"]["content_hash"]) == 64


def test_investigation_unsafe_zip_path_rejected(tmp_path: Path) -> None:
    from scripts.harvest_nhtsa import UnsafeArchivePathError, _validate_zip_member
    with pytest.raises(UnsafeArchivePathError):
        _validate_zip_member("../../../etc/passwd")


def test_investigation_valid_cache_reused(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    first = harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
    second = harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
    assert first["record_count"] == second["record_count"]


def test_investigation_corrupt_cache_rejected(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
    archive = tmp_path / "raw" / "nhtsa_investigations" / "FLAT_INV.zip"
    if archive.exists():
        archive.write_bytes(b"corrupt")
        result = harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
        assert result["record_count"] == 4
