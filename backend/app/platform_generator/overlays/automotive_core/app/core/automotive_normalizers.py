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

from app.models.automotive_records import AutomotiveRecall


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
