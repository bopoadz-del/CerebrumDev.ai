"""Deterministic normalization of raw NHTSA rows into canonical records.

This module is intentionally free of vector-store, embedding and RAG
retrieval dependencies so it can run in the factory and in the generated
platform.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.automotive_records import (
    AutomotiveComplaint,
    AutomotiveInvestigation,
    AutomotiveRecall,
    AutomotiveSafetyRating,
)


UNKNOWN_YEAR_TOKENS = {"9999", "99999", "0000", "", "N/A", "NA"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_text(value: Optional[str], max_chars: int = 6000) -> Optional[str]:
    if value is None:
        return None
    value = value.replace("\x00", "").replace("\r", " ").replace("\n", " ")
    value = "".join(ch for ch in value if ch == "\t" or ord(ch) >= 32)
    value = value.strip()
    if value in {"", "N/A", "NA", "NULL", "null"}:
        return None
    if len(value) > max_chars:
        value = value[:max_chars]
    return value if value else None


def _parse_date(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value or value == "99999999":
        return None
    if len(value) == 8 and value.isdigit():
        try:
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        except ValueError:
            return None
    return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    value = (value or "").strip().replace(",", "").replace(" ", "")
    if not value or not value.isdigit():
        return None
    return int(value)


def _field_getter(row: Dict[str, str]):
    lower = {k.lower(): k for k in row}

    def get(*keys: str) -> Optional[str]:
        for key in keys:
            if key in row:
                return row[key]
            if key.lower() in lower:
                return row[lower[key.lower()]]
        return None

    return get


def _record_id(source_id: str, record_sequence: int, campno: str) -> str:
    data = f"{source_id}:{record_sequence}:{campno}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _raw_record_hash(source_id: str, row: Dict[str, str]) -> str:
    data = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{source_id}:{data}".encode("utf-8")).hexdigest()


def normalize_recall_row(
    source_id: str,
    record_sequence: int,
    row: Dict[str, str],
) -> AutomotiveRecall:
    """Convert one raw NHTSA recall row into a canonical record."""
    get = _field_getter(row)

    campaign = _clean_text(get("CAMPNO", "campno"), max_chars=64) or ""
    manufacturer = _clean_text(get("MFGNAME", "mfgname", "MFGTXT", "mfgtxt"), max_chars=256)
    make = _clean_text(get("MAKETXT", "maketxt"), max_chars=128)
    model = _clean_text(get("MODELTXT", "modeltxt"), max_chars=512)
    year = _clean_text(get("YEARTXT", "yeartxt"), max_chars=16)
    if year in UNKNOWN_YEAR_TOKENS:
        year = None
    component = _clean_text(get("COMPNAME", "compname"), max_chars=512)
    summary = _clean_text(get("DESC_DEFECT", "desc_defect", "DESC"), max_chars=6000)
    consequence = _clean_text(get("CONEQUENCE_DEFECT", "conequence_defect", "CONSEQUENCE"), max_chars=2000)
    remedy = _clean_text(get("CORRECTIVE_ACTION", "corrective_action", "REMEDY"), max_chars=6000)
    report_received_date = _parse_date(get("RCDATE", "rcdate"))
    affected_units = _parse_int(get("POTAFF", "potaff"))

    record_id = _record_id(source_id, record_sequence, campaign)
    raw_hash = _raw_record_hash(source_id, row)

    return AutomotiveRecall(
        record_id=record_id,
        source_id=source_id,
        source_family="recall",
        campaign_number=campaign,
        manufacturer=manufacturer,
        make=make,
        model=model,
        model_year=year,
        component=component,
        summary=summary,
        consequence=consequence,
        remedy=remedy,
        report_received_date=report_received_date,
        affected_units=affected_units,
        source_url=f"https://www.nhtsa.gov/recalls?nhtsaId={campaign}" if campaign else None,
        jurisdiction="US",
        authority_rating="primary",
        harvest_timestamp=_utc_now(),
        raw_record_hash=raw_hash,
        normalization_version="automotive_core_v1",
    )


def normalize_recall_rows(
    source_id: str,
    rows: List[Dict[str, str]],
) -> List[AutomotiveRecall]:
    """Normalize a list of raw recall rows."""
    return [normalize_recall_row(source_id, i + 1, row) for i, row in enumerate(rows)]


def _investigation_type(action_number: str) -> Optional[str]:
    """Extract alphabetic prefix from NHTSA action number."""
    if not action_number:
        return None
    prefix = ""
    for ch in action_number:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix.upper() or None


def _investigation_status(cdate: Optional[str]) -> Optional[str]:
    """Infer open/closed status from closing date."""
    if not cdate or cdate.strip() in {"", "99999999"}:
        return "Open"
    return "Closed"


def normalize_investigation_row(
    source_id: str,
    record_sequence: int,
    row: Dict[str, str],
) -> AutomotiveInvestigation:
    """Convert one raw NHTSA ODI investigation row into a canonical record."""
    get = _field_getter(row)

    investigation_number = _clean_text(get("NHTSA_ACTION_NUMBER", "nhtsa_action_number", "action_number"), max_chars=32) or ""
    make = _clean_text(get("MAKE", "make", "MAKETXT"), max_chars=128)
    model = _clean_text(get("MODEL", "model", "MODELTXT"), max_chars=512)
    year = _clean_text(get("YEAR", "year", "YEARTXT"), max_chars=16)
    if year in UNKNOWN_YEAR_TOKENS:
        year = None
    component = _clean_text(get("COMPNAME", "compname", "COMPONENT"), max_chars=512)
    manufacturer = _clean_text(get("MFR_NAME", "mfr_name", "MFGNAME", "manufacturer"), max_chars=256)
    opening_date = _parse_date(get("ODATE", "odate", "OPENING_DATE"))
    closing_date = _parse_date(get("CDATE", "cdate", "CLOSING_DATE"))
    associated_campaign = _clean_text(get("CAMPNO", "campno", "RECALL_NUMBER"), max_chars=32)
    subject = _clean_text(get("SUBJECT", "subject", "TOPIC"), max_chars=200)
    summary = _clean_text(get("SUMMARY", "summary", "DETAIL"), max_chars=6000)

    record_id = _record_id(source_id, record_sequence, investigation_number)
    raw_hash = _raw_record_hash(source_id, row)

    return AutomotiveInvestigation(
        record_id=record_id,
        source_id=source_id,
        source_family="investigation",
        investigation_number=investigation_number,
        status=_investigation_status(closing_date),
        investigation_type=_investigation_type(investigation_number),
        make=make,
        model=model,
        model_year=year,
        component=component,
        manufacturer=manufacturer,
        subject=subject,
        summary=summary,
        opening_date=opening_date,
        closing_date=closing_date,
        associated_campaign_number=associated_campaign or None,
        source_url=f"https://www.nhtsa.gov/nhtsa-datasets-and-apis" if investigation_number else None,
        jurisdiction="US",
        authority_rating="primary",
        harvest_timestamp=_utc_now(),
        raw_record_hash=raw_hash,
        normalization_version="automotive_core_v1",
    )


def normalize_investigation_rows(
    source_id: str,
    rows: List[Dict[str, str]],
) -> List[AutomotiveInvestigation]:
    """Normalize a list of raw ODI investigation rows."""
    return [normalize_investigation_row(source_id, i + 1, row) for i, row in enumerate(rows)]


def _parse_bool_flag(value: Optional[str]) -> Optional[bool]:
    cleaned = (value or "").strip().upper()
    if cleaned in {"Y", "YES", "TRUE", "1"}:
        return True
    if cleaned in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def normalize_complaint_row(
    source_id: str,
    record_sequence: int,
    row: Dict[str, str],
) -> AutomotiveComplaint:
    """Convert one raw NHTSA consumer complaint row into a canonical record."""
    get = _field_getter(row)

    complaint_id = (
        _clean_text(get("ODINO", "odino", "CMPLID", "cmplid", "COMPLAINT_ID"), max_chars=32) or ""
    )
    make = _clean_text(get("MAKETXT", "maketxt", "MAKE"), max_chars=128)
    model = _clean_text(get("MODELTXT", "modeltxt", "MODEL"), max_chars=512)
    year = _clean_text(get("YEARTXT", "yeartxt", "YEAR"), max_chars=16)
    if year in UNKNOWN_YEAR_TOKENS:
        year = None
    component = _clean_text(get("COMPNAME", "compname", "COMPONENT"), max_chars=512)
    manufacturer = _clean_text(get("MFR_NAME", "mfr_name", "MFGNAME"), max_chars=256)
    summary = _clean_text(get("CDESCR", "cdescr", "DESC", "SUMMARY"), max_chars=6000)
    associated_campaign = _clean_text(get("CMPL_RECALL", "cmpl_recall", "CAMPNO"), max_chars=32)
    date_complaint = _parse_date(get("DATEA", "datea", "DATE_COMPLAINT"))

    record_id = _record_id(source_id, record_sequence, complaint_id)
    raw_hash = _raw_record_hash(source_id, row)

    return AutomotiveComplaint(
        record_id=record_id,
        source_id=source_id,
        source_family="complaint",
        complaint_id=complaint_id,
        make=make,
        model=model,
        model_year=year,
        component=component,
        manufacturer=manufacturer,
        summary=summary,
        crash=_parse_bool_flag(get("CRASH", "crash")),
        fire=_parse_bool_flag(get("FIRE", "fire")),
        injured=_parse_int(get("INJURED", "injured")),
        deaths=_parse_int(get("DEATHS", "deaths")),
        date_complaint=date_complaint,
        associated_campaign_number=associated_campaign or None,
        source_url=(
            f"https://www.nhtsa.gov/vehicle/{complaint_id}" if complaint_id else None
        ),
        jurisdiction="US",
        authority_rating="primary",
        harvest_timestamp=_utc_now(),
        raw_record_hash=raw_hash,
        normalization_version="automotive_core_v1",
    )


def normalize_complaint_rows(
    source_id: str,
    rows: List[Dict[str, str]],
) -> List[AutomotiveComplaint]:
    """Normalize a list of raw consumer complaint rows."""
    return [normalize_complaint_row(source_id, i + 1, row) for i, row in enumerate(rows)]


def normalize_safety_rating_row(
    source_id: str,
    record_sequence: int,
    row: Dict[str, str],
) -> AutomotiveSafetyRating:
    """Convert one raw NHTSA safety-rating row into a canonical record."""
    get = _field_getter(row)

    vehicle_id = _clean_text(get("VehicleId", "vehicleid", "VEHICLE_ID", "ID"), max_chars=32) or ""
    make = _clean_text(get("Make", "make", "MAKETXT"), max_chars=128)
    model = _clean_text(get("Model", "model", "MODELTXT"), max_chars=512)
    year = _clean_text(get("ModelYear", "modelyear", "YEAR", "YEARTXT"), max_chars=16)
    if year in UNKNOWN_YEAR_TOKENS:
        year = None
    overall = _clean_text(get("OverallRating", "overallrating"), max_chars=8)
    front = _clean_text(get("OverallFrontCrashRating", "overallfrontcrashrating"), max_chars=8)
    side = _clean_text(get("OverallSideCrashRating", "overallsidecrashrating"), max_chars=8)
    rollover = _clean_text(get("RolloverRating", "rolloverrating"), max_chars=8)
    description = _clean_text(get("VehicleDescription", "vehicledescription"), max_chars=512)

    record_id = _record_id(source_id, record_sequence, vehicle_id)
    raw_hash = _raw_record_hash(source_id, row)

    return AutomotiveSafetyRating(
        record_id=record_id,
        source_id=source_id,
        source_family="safety_rating",
        vehicle_id=vehicle_id,
        make=make,
        model=model,
        model_year=year,
        overall_rating=overall,
        front_crash_rating=front,
        side_crash_rating=side,
        rollover_rating=rollover,
        vehicle_description=description,
        source_url=(
            f"https://www.nhtsa.gov/vehicle/{year}/{make}/{model}".replace(" ", "%20")
            if year and make and model
            else None
        ),
        jurisdiction="US",
        authority_rating="primary",
        harvest_timestamp=_utc_now(),
        raw_record_hash=raw_hash,
        normalization_version="automotive_core_v1",
    )


def normalize_safety_rating_rows(
    source_id: str,
    rows: List[Dict[str, str]],
) -> List[AutomotiveSafetyRating]:
    """Normalize a list of raw safety-rating rows."""
    return [normalize_safety_rating_row(source_id, i + 1, row) for i, row in enumerate(rows)]
