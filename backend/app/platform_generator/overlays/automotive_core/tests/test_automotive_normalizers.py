"""Tests for automotive canonical record normalizers."""

from __future__ import annotations

from app.core.automotive_normalizers import (
    normalize_complaint_row,
    normalize_investigation_row,
    normalize_investigation_rows,
    normalize_recall_row,
    normalize_recall_rows,
    normalize_safety_rating_row,
)
from app.models.automotive_records import AutomotiveRecall


def test_normalize_recall_row_maps_official_columns() -> None:
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
    assert record.campaign_number == "15V176000"
    assert record.make == "Honda"
    assert record.model == "Accord"
    assert record.model_year == "2014"
    assert record.report_received_date == "2015-03-31"
    assert record.affected_units == 1900000


def test_normalize_recall_row_handles_unknown_year() -> None:
    row = {
        "CAMPNO": "99V999000",
        "MAKETXT": "Unknown",
        "YEARTXT": "9999",
        "RCDATE": "99999999",
    }
    record = normalize_recall_row("nhtsa_recalls", 1, row)
    assert record.model_year is None
    assert record.report_received_date is None


def test_normalize_recall_row_strips_control_characters() -> None:
    row = {
        "CAMPNO": "15V176000",
        "MAKETXT": "Honda\x00\x01",
        "DESC_DEFECT": "Summary with\nnewlines\rand\ttabs.",
    }
    record = normalize_recall_row("nhtsa_recalls", 1, row)
    assert "\x00" not in record.make
    assert "\n" not in record.summary
    assert "\r" not in record.summary


def test_normalize_recall_rows_are_deterministic() -> None:
    rows = [
        {"CAMPNO": "15V176000", "MAKETXT": "Honda", "YEARTXT": "2014"},
        {"CAMPNO": "21V127000", "MAKETXT": "Toyota", "YEARTXT": "2018"},
    ]
    first = normalize_recall_rows("nhtsa_recalls", rows)
    second = normalize_recall_rows("nhtsa_recalls", rows)
    assert [r.record_id for r in first] == [r.record_id for r in second]
    assert [r.raw_record_hash for r in first] == [r.raw_record_hash for r in second]


def test_missing_values_remain_null() -> None:
    row = {"CAMPNO": "15V176000"}
    record = normalize_recall_row("nhtsa_recalls", 1, row)
    assert record.manufacturer is None
    assert record.component is None
    assert record.summary is None
    assert record.consequence is None
    assert record.remedy is None


def test_automotive_investigation_model_exists() -> None:
    from app.models.automotive_records import AutomotiveInvestigation

    record = AutomotiveInvestigation(
        record_id="abc123",
        source_id="nhtsa_investigations",
        source_family="investigation",
        investigation_number="PE16-007",
        harvest_timestamp="2026-07-12T00:00:00Z",
        raw_record_hash="def456",
    )
    assert record.investigation_number == "PE16-007"
    assert record.source_family == "investigation"


def test_normalize_investigation_row_maps_official_columns() -> None:
    row = {
        "NHTSA_ACTION_NUMBER": "PE16-007",
        "MAKE": "Tesla",
        "MODEL": "Model S",
        "YEAR": "2015",
        "COMPNAME": "AIR BAGS",
        "MFR_NAME": "Tesla Motors",
        "ODATE": "20160101",
        "CDATE": "20161231",
        "CAMPNO": "16V176000",
        "SUBJECT": "Air bag deployment investigation",
        "SUMMARY": "Investigation into unintended air bag deployment events.",
    }
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.investigation_number == "PE16-007"
    assert record.make == "Tesla"
    assert record.model == "Model S"
    assert record.model_year == "2015"
    assert record.component == "AIR BAGS"
    assert record.manufacturer == "Tesla Motors"
    assert record.opening_date == "2016-01-01"
    assert record.closing_date == "2016-12-31"
    assert record.associated_campaign_number == "16V176000"
    assert record.subject == "Air bag deployment investigation"
    assert record.summary == "Investigation into unintended air bag deployment events."
    assert record.status == "Closed"
    assert record.investigation_type == "PE"



def test_investigation_status_open_when_no_cdate() -> None:
    row = {"NHTSA_ACTION_NUMBER": "DP20-001", "CDATE": ""}
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.status == "Open"


def test_investigation_status_closed_when_cdate_present() -> None:
    row = {"NHTSA_ACTION_NUMBER": "PE16-007", "CDATE": "20161231"}
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.status == "Closed"


def test_investigation_missing_values_remain_null() -> None:
    row = {"NHTSA_ACTION_NUMBER": "PE16-007"}
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.make is None
    assert record.model is None
    assert record.model_year is None
    assert record.component is None
    assert record.summary is None


def test_investigation_deterministic_id_and_hash() -> None:
    rows = [
        {"NHTSA_ACTION_NUMBER": "PE16-007", "MAKE": "Tesla", "YEAR": "2015"},
        {"NHTSA_ACTION_NUMBER": "DP20-001", "MAKE": "Ford", "YEAR": "2018"},
    ]
    first = normalize_investigation_rows("nhtsa_investigations", rows)
    second = normalize_investigation_rows("nhtsa_investigations", rows)
    assert [r.record_id for r in first] == [r.record_id for r in second]
    assert [r.raw_record_hash for r in first] == [r.raw_record_hash for r in second]


def test_investigation_control_characters_removed() -> None:
    row = {
        "NHTSA_ACTION_NUMBER": "PE16-007",
        "MAKE": "Tesla\x00\x01",
        "SUMMARY": "Summary with\nnewlines\rand\ttabs.",
    }
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert "\x00" not in record.make
    assert "\n" not in record.summary
    assert "\r" not in record.summary


def test_normalize_complaint_row_maps_official_columns() -> None:
    row = {
        "ODINO": "100001",
        "MAKETXT": "Honda",
        "MODELTXT": "Accord",
        "YEARTXT": "2014",
        "COMPNAME": "AIR BAGS",
        "CDESCR": "Air bag warning light.",
        "CRASH": "N",
        "FIRE": "N",
        "INJURED": "0",
        "DEATHS": "0",
        "DATEA": "20150401",
        "CMPL_RECALL": "15V176000",
    }
    record = normalize_complaint_row("nhtsa_complaints", 1, row)
    assert record.complaint_id == "100001"
    assert record.source_family == "complaint"
    assert record.associated_campaign_number == "15V176000"
    assert record.crash is False


def test_normalize_safety_rating_row_maps_official_columns() -> None:
    row = {
        "VehicleId": "12345",
        "Make": "Honda",
        "Model": "Accord",
        "ModelYear": "2014",
        "OverallRating": "5",
        "OverallFrontCrashRating": "5",
        "OverallSideCrashRating": "5",
        "RolloverRating": "4",
        "VehicleDescription": "2014 Honda Accord 4 DR FWD",
    }
    record = normalize_safety_rating_row("nhtsa_safety_ratings", 1, row)
    assert record.vehicle_id == "12345"
    assert record.source_family == "safety_rating"
    assert record.overall_rating == "5"
