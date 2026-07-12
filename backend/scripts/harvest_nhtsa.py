"""NHTSA official-data harvester for the Automotive Safety Intelligence Pilot.

Public-data source:
    https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip
    https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt (data dictionary)

The harvester is network-safe: it streams downloads, rejects unsafe ZIP paths,
records SHA-256 hashes, and can replay from an authored fixture for tests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RECALL_URL = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip"
DEFAULT_DATA_DICT_URL = "https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt"

EXPECTED_ARCHIVE_MEMBERS = {
    "FLAT_RCL_POST_2010.txt",
    "FLAT_RCL.txt",  # some historical archives use the un-suffixed name
}

# Official NHTSA recall flat-file layout (May 2025 data dictionary).
# The bulk file is tab-delimited and has NO header row.
NHTSA_RECALL_FIELDNAMES = [
    "RECORD_ID",
    "CAMPNO",
    "MAKETXT",
    "MODELTXT",
    "YEARTXT",
    "MFGCAMPNO",
    "COMPNAME",
    "MFGNAME",
    "BGMAN",
    "ENDMAN",
    "RCLTYPECD",
    "POTAFF",
    "ODATE",
    "INFLUENCED_BY",
    "MFGTXT",
    "RCDATE",
    "DATEA",
    "RPNO",
    "FMVSS",
    "DESC_DEFECT",
    "CONEQUENCE_DEFECT",
    "CORRECTIVE_ACTION",
    "NOTES",
    "RCL_CMPT_ID",
    "MFR_COMP_NAME",
    "MFR_COMP_DESC",
    "MFR_COMP_PTNO",
    "DO_NOT_DRIVE",
    "PARK_OUTSIDE",
]

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

DEFAULT_INVESTIGATION_URL = "https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip"
DEFAULT_INVESTIGATION_DICT_URL = "https://static.nhtsa.gov/odi/ffdd/inv/INV.txt"

EXPECTED_INVESTIGATION_ARCHIVE_MEMBERS = {"FLAT_INV.txt"}

# Official NHTSA ODI investigation flat-file layout.
# The bulk file is tab-delimited and has NO header row.
NHTSA_INVESTIGATION_FIELDNAMES = [
    "NHTSA_ACTION_NUMBER",
    "MAKE",
    "MODEL",
    "YEAR",
    "COMPNAME",
    "MFR_NAME",
    "ODATE",
    "CDATE",
    "CAMPNO",
    "SUBJECT",
    "SUMMARY",
]


class HarvestError(Exception):
    """Raised when harvesting fails in a non-recoverable way."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class UnsafeArchivePathError(HarvestError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_dir() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_id(source_id: str, record_sequence: int, campno: str) -> str:
    """Deterministic record identity from stable source identifiers."""
    data = f"{source_id}:{record_sequence}:{campno}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _raw_record_hash(source_id: str, row: Dict[str, str]) -> str:
    """Deterministic hash of the raw official row."""
    data = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{source_id}:{data}".encode("utf-8")).hexdigest()


def _clean_text(value: Optional[str], max_chars: int = 6000) -> Optional[str]:
    """Strip control chars, bound length, return None for empty/unknown."""
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
    """Normalize NHTSA YYYYMMDD strings to ISO date when possible."""
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


def normalize_recall_row(
    source_id: str,
    record_sequence: int,
    row: Dict[str, str],
) -> Dict[str, Any]:
    """Convert a raw NHTSA recall row into a canonical automotive record dict.

    Defensive aliases: the data dictionary labels are authoritative, but we
    accept several common casings so a renamed upstream file does not silently
    break ingestion.
    """
    get = _field_getter(row)

    campaign = _clean_text(get("CAMPNO", "campno"), max_chars=64) or ""
    manufacturer = _clean_text(get("MFGNAME", "mfgname", "MFGTXT", "mfgtxt"), max_chars=256)
    make = _clean_text(get("MAKETXT", "maketxt"), max_chars=128)
    model = _clean_text(get("MODELTXT", "modeltxt"), max_chars=512)
    year = _clean_text(get("YEARTXT", "yeartxt"), max_chars=16)
    if year in ("9999", "99999", "0000"):
        year = None
    component = _clean_text(get("COMPNAME", "compname"), max_chars=512)
    summary = _clean_text(get("DESC_DEFECT", "desc_defect", "DESC"), max_chars=6000)
    consequence = _clean_text(get("CONEQUENCE_DEFECT", "conequence_defect", "CONSEQUENCE"), max_chars=2000)
    remedy = _clean_text(get("CORRECTIVE_ACTION", "corrective_action", "REMEDY"), max_chars=6000)
    report_received_date = _parse_date(get("RCDATE", "rcdate"))
    affected_units = _parse_int(get("POTAFF", "potaff"))

    record_id = _record_id(source_id, record_sequence, campaign)
    raw_hash = _raw_record_hash(source_id, row)

    return {
        "record_id": record_id,
        "source_id": source_id,
        "source_family": "recall",
        "campaign_number": campaign,
        "manufacturer": manufacturer,
        "make": make,
        "model": model,
        "model_year": year,
        "component": component,
        "summary": summary,
        "consequence": consequence,
        "remedy": remedy,
        "report_received_date": report_received_date,
        "affected_units": affected_units,
        "source_url": f"https://www.nhtsa.gov/recalls?nhtsaId={campaign}" if campaign else None,
        "jurisdiction": "US",
        "authority_rating": "primary",
        "harvest_timestamp": _utc_now(),
        "raw_record_hash": raw_hash,
        "normalization_version": "automotive_core_v1",
    }


def _field_getter(row: Dict[str, str]):
    """Return a case-insensitive getter for the row dict.

    Filters out ``None`` keys that ``csv.DictReader`` creates when a row has
    more trailing fields than the header (common in tab-delimited NHTSA data).
    """
    lower = {k.lower(): k for k in row if k is not None}

    def get(*keys: str) -> Optional[str]:
        for key in keys:
            if key in row:
                return row[key]
            if key.lower() in lower:
                return row[lower[key.lower()]]
        return None

    return get


_normalize_investigation_row: Optional[Any] = None


def _import_overlay_investigation_normalizer() -> Any:
    """Load ``normalize_investigation_row`` from the automotive_core overlay.

    The harvester runs from ``backend/`` where ``app`` is the backend package.
    The overlay also uses the ``app`` namespace package, so importing it directly
    would collide with the backend ``app`` tree. We temporarily swap the relevant
    ``sys.modules`` entries, load the overlay modules, then restore the original
    backend ``app`` tree so the rest of the codebase remains unaffected.
    """
    overlay_dir = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "platform_generator"
        / "overlays"
        / "automotive_core"
    )
    if not overlay_dir.exists():
        raise ImportError(f"automotive_core overlay not found at {overlay_dir}")

    normalizer_path = overlay_dir / "app" / "core" / "automotive_normalizers.py"
    models_path = overlay_dir / "app" / "models" / "automotive_records.py"

    touched = [
        "app",
        "app.models",
        "app.models.automotive_records",
        "app.core",
        "app.core.automotive_normalizers",
    ]
    saved: Dict[str, Any] = {}
    for key in touched:
        saved[key] = sys.modules.pop(key, None)

    try:
        models_spec = importlib.util.spec_from_file_location(
            "app.models.automotive_records", models_path
        )
        if models_spec is None:
            raise ImportError(
                f"Could not create module spec for overlay models at {models_path}"
            )
        models_mod = importlib.util.module_from_spec(models_spec)
        sys.modules["app.models.automotive_records"] = models_mod

        app_mod = types.ModuleType("app")
        app_models_mod = types.ModuleType("app.models")
        sys.modules["app"] = app_mod
        sys.modules["app.models"] = app_models_mod
        app_mod.models = app_models_mod
        app_models_mod.automotive_records = models_mod
        models_spec.loader.exec_module(models_mod)

        norm_spec = importlib.util.spec_from_file_location(
            "app.core.automotive_normalizers", normalizer_path
        )
        if norm_spec is None:
            raise ImportError(
                f"Could not create module spec for overlay normalizer at {normalizer_path}"
            )
        norm_mod = importlib.util.module_from_spec(norm_spec)
        app_core_mod = types.ModuleType("app.core")
        sys.modules["app.core"] = app_core_mod
        sys.modules["app.core.automotive_normalizers"] = norm_mod
        app_mod.core = app_core_mod
        app_core_mod.automotive_normalizers = norm_mod
        norm_spec.loader.exec_module(norm_mod)

        return norm_mod.normalize_investigation_row
    finally:
        for key in touched:
            if saved.get(key) is not None:
                sys.modules[key] = saved[key]
            elif key in sys.modules:
                del sys.modules[key]


def _get_normalize_investigation_row() -> Any:
    """Return the cached overlay investigation normalizer, loading it once."""
    global _normalize_investigation_row
    if _normalize_investigation_row is None:
        try:
            from app.core.automotive_normalizers import normalize_investigation_row
        except ImportError:
            normalize_investigation_row = _import_overlay_investigation_normalizer()
        _normalize_investigation_row = normalize_investigation_row
    return _normalize_investigation_row


def _download_stream(
    url: str,
    dest: Path,
    timeout: float = 120.0,
    max_retries: int = 3,
) -> None:
    """Stream ``url`` to ``dest`` with bounded retries and backoff.

    Writes to a temporary file next to ``dest`` and atomically replaces on
    success so an interrupted download never publishes a partial file.
    """
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
                if response.status_code == 404:
                    raise HarvestError("SOURCE_NOT_FOUND", f"Source returned 404: {url}")
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise HarvestError(
                        "SOURCE_TEMPORARILY_UNAVAILABLE",
                        f"Source returned {response.status_code}: {url}",
                    )
                response.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
            os.replace(tmp, dest)
            return
        except (HarvestError, httpx.HTTPError, httpx.TimeoutException) as exc:
            last_error = exc
            logger.warning("Download attempt %d/%d failed for %s: %s", attempt + 1, max_retries, url, exc)
            if attempt < max_retries - 1:
                import time

                time.sleep(2 ** attempt)
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    raise HarvestError("DOWNLOAD_FAILED", f"Failed to download {url} after {max_retries} attempts: {last_error}")


def _validate_zip_member(name: str) -> str:
    """Reject ZIP paths that escape the extraction root."""
    target = Path(name)
    if ".." in target.parts:
        raise UnsafeArchivePathError("UNSAFE_ZIP_PATH", f"Unsafe archive member: {name}")
    # ZIP entries may use Unix-style absolute paths or Windows drive letters.
    stripped = name.lstrip("/\\")
    if len(stripped) != len(name) or (len(name) > 1 and name[1] == ":"):
        raise UnsafeArchivePathError("UNSAFE_ZIP_PATH", f"Unsafe archive member: {name}")
    return name


def extract_recall_csv(zip_path: Path) -> Path:
    """Extract the recall flat file from the archive and return its path.

    The function returns the extracted file path; the caller decides where to
    keep it long-term. The extracted file is written to the same directory as
    the archive so atomic rename semantics remain simple.
    """
    extract_dir = zip_path.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        candidates = [
            info for info in zf.infolist()
            if not info.is_dir() and Path(info.filename).name in EXPECTED_ARCHIVE_MEMBERS
        ]
        if not candidates:
            names = [info.filename for info in zf.infolist() if not info.is_dir()]
            raise HarvestError("ARCHIVE_UNEXPECTED_CONTENT", f"No expected recall file in archive: {names}")

        chosen = sorted(candidates, key=lambda i: i.file_size, reverse=True)[0]
        safe_name = _validate_zip_member(chosen.filename)
        dest = extract_dir / Path(safe_name).name
        with zf.open(chosen) as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return dest


def _count_rows(csv_path: Path) -> int:
    count = 0
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, fieldnames=NHTSA_RECALL_FIELDNAMES, delimiter="\t")
        for _ in reader:
            count += 1
    return count


def _is_header_row(row: Dict[str, str]) -> bool:
    """Detect a header row in tab-delimited fixtures that include column names.

    The official bulk file has no header, but authored test fixtures often do.
    A row whose RECORD_ID/CAMPNO values are the literal column names is a header.
    """
    return row.get("RECORD_ID") == "RECORD_ID" and row.get("CAMPNO") == "CAMPNO"


def _load_fixture(fixture_path: Path) -> List[Dict[str, str]]:
    """Load an authored fixture CSV/JSONL representing the real NHTSA schema."""
    suffix = fixture_path.suffix.lower()
    if suffix == ".jsonl":
        rows = []
        with fixture_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    if suffix in {".csv", ".txt"}:
        rows = []
        with fixture_path.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, fieldnames=NHTSA_RECALL_FIELDNAMES, delimiter="\t")
            for idx, row in enumerate(reader):
                if idx == 0 and _is_header_row(row):
                    continue
                rows.append({k: v for k, v in row.items() if k is not None})
        return rows
    raise HarvestError("UNSUPPORTED_FIXTURE", f"Fixture must be .jsonl, .csv or .txt: {fixture_path}")


def _rows_from_csv(csv_path: Path) -> List[Dict[str, str]]:
    rows = []
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(
            f,
            fieldnames=NHTSA_RECALL_FIELDNAMES,
            delimiter="\t",
        )
        for row in reader:
            # Drop trailing ``None`` keys produced when a row has more fields
            # than the expected layout (tab-delimited sources often end with tabs).
            rows.append({k: v for k, v in row.items() if k is not None})
    return rows


def extract_investigation_csv(zip_path: Path) -> Path:
    """Extract the investigation flat file from the archive."""
    extract_dir = zip_path.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        candidates = [
            info for info in zf.infolist()
            if not info.is_dir() and Path(info.filename).name in EXPECTED_INVESTIGATION_ARCHIVE_MEMBERS
        ]
        if not candidates:
            names = [info.filename for info in zf.infolist() if not info.is_dir()]
            raise HarvestError("ARCHIVE_UNEXPECTED_CONTENT", f"No expected investigation file in archive: {names}")

        chosen = sorted(candidates, key=lambda i: i.file_size, reverse=True)[0]
        safe_name = _validate_zip_member(chosen.filename)
        dest = extract_dir / Path(safe_name).name
        with zf.open(chosen) as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return dest


def _investigation_rows_from_csv(csv_path: Path) -> List[Dict[str, str]]:
    rows = []
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(
            f,
            fieldnames=NHTSA_INVESTIGATION_FIELDNAMES,
            delimiter="\t",
        )
        for row in reader:
            rows.append({k: v for k, v in row.items() if k is not None})
    return rows


def _load_investigation_fixture(fixture_path: Path) -> List[Dict[str, str]]:
    suffix = fixture_path.suffix.lower()
    if suffix == ".jsonl":
        rows = []
        with fixture_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    if suffix in {".csv", ".txt"}:
        rows = []
        with fixture_path.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, fieldnames=NHTSA_INVESTIGATION_FIELDNAMES, delimiter="\t")
            for idx, row in enumerate(reader):
                if idx == 0 and row.get("NHTSA_ACTION_NUMBER") == "NHTSA_ACTION_NUMBER":
                    continue
                rows.append({k: v for k, v in row.items() if k is not None})
        return rows
    raise HarvestError("UNSUPPORTED_FIXTURE", f"Fixture must be .jsonl, .csv or .txt: {fixture_path}")


def harvest_recalls(
    output_dir: Path,
    source_url: Optional[str] = None,
    since_year: Optional[int] = None,
    max_records: Optional[int] = None,
    dry_run: bool = False,
    fixture_path: Optional[Path] = None,
    force_download: bool = False,
) -> Dict[str, Any]:
    """Harvest NHTSA recalls and write raw artifacts plus a harvest manifest.

    Returns a dict describing the harvest, including the canonical records path.
    """
    source_id = "nhtsa_recalls"
    url = source_url or DEFAULT_RECALL_URL
    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw" / source_id
    canonical_dir = output_dir / "canonical"
    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)
        canonical_dir.mkdir(parents=True, exist_ok=True)

    archive_path = raw_dir / Path(urlparse(url).path).name
    extracted_csv: Optional[Path] = None
    content_hash = ""

    if fixture_path:
        logger.info("Using fixture %s for %s", fixture_path, source_id)
        rows = _load_fixture(fixture_path)
        content_hash = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        fixture_copy = raw_dir / f"fixture{fixture_path.suffix}"
        if not dry_run:
            shutil.copy2(fixture_path, fixture_copy)
    else:
        if not dry_run:
            if archive_path.exists() and not force_download:
                content_hash = _sha256_file(archive_path)
                logger.info("Reusing existing archive %s (sha256=%s)", archive_path, content_hash)
            else:
                _download_stream(url, archive_path)
                content_hash = _sha256_file(archive_path)
                logger.info("Downloaded archive %s (sha256=%s)", archive_path, content_hash)
            extracted_csv = extract_recall_csv(archive_path)
            rows = _rows_from_csv(extracted_csv)
        else:
            rows = []

    if since_year is not None:
        filtered = []
        for row in rows:
            year_str = _field_getter(row)("YEARTXT", "yeartxt") or ""
            year_val = _parse_int(year_str)
            if year_val is not None and year_val >= since_year:
                filtered.append(row)
        rows = filtered

    if max_records is not None and len(rows) > max_records:
        logger.info("Capping harvest at %d records (raw rows available: %d)", max_records, len(rows))
        rows = rows[:max_records]

    canonical_records: List[Dict[str, Any]] = []
    for sequence, row in enumerate(rows, start=1):
        canonical = normalize_recall_row(source_id, sequence, row)
        canonical_records.append(canonical)

    harvest_manifest = {
        "source_id": source_id,
        "source_family": "recall",
        "source_url": url,
        "retrieval_method": "download_csv_zip",
        "harvest_timestamp": _utc_now(),
        "content_hash": content_hash,
        "record_count": len(canonical_records),
        "raw_archive_path": str(archive_path.relative_to(output_dir)) if not fixture_path else None,
        "extracted_csv_path": str(extracted_csv.relative_to(output_dir)) if extracted_csv else None,
        "fixture_path": str(fixture_path.name) if fixture_path else None,
        "since_year": since_year,
    }

    canonical_path = canonical_dir / "recalls.jsonl"
    manifest_path = output_dir / "harvest_manifest.json"

    if not dry_run:
        tmp_canonical = canonical_path.with_suffix(".jsonl.tmp")
        with tmp_canonical.open("w", encoding="utf-8") as f:
            for record in canonical_records:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp_canonical, canonical_path)

        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_manifest.write_text(
            json.dumps(harvest_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_manifest, manifest_path)
    else:
        logger.info("Dry run: would write %d canonical records to %s", len(canonical_records), canonical_path)

    return {
        "harvest_manifest": harvest_manifest,
        "canonical_path": canonical_path if not dry_run else None,
        "record_count": len(canonical_records),
    }


def harvest_investigations(
    output_dir: Path,
    source_url: Optional[str] = None,
    since_year: Optional[int] = None,
    max_records: Optional[int] = None,
    dry_run: bool = False,
    fixture_path: Optional[Path] = None,
    force_download: bool = False,
) -> Dict[str, Any]:
    """Harvest NHTSA ODI investigations and write raw artifacts + harvest manifest."""
    source_id = "nhtsa_investigations"
    url = source_url or DEFAULT_INVESTIGATION_URL
    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw" / source_id
    canonical_dir = output_dir / "canonical"
    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)
        canonical_dir.mkdir(parents=True, exist_ok=True)

    archive_path = raw_dir / Path(urlparse(url).path).name
    extracted_csv: Optional[Path] = None
    content_hash = ""

    if fixture_path:
        logger.info("Using fixture %s for %s", fixture_path, source_id)
        rows = _load_investigation_fixture(fixture_path)
        content_hash = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        fixture_copy = raw_dir / f"fixture{fixture_path.suffix}"
        if not dry_run:
            shutil.copy2(fixture_path, fixture_copy)
    else:
        if not dry_run:
            if archive_path.exists() and not force_download:
                content_hash = _sha256_file(archive_path)
                logger.info("Reusing existing archive %s (sha256=%s)", archive_path, content_hash)
            else:
                _download_stream(url, archive_path)
                content_hash = _sha256_file(archive_path)
                logger.info("Downloaded archive %s (sha256=%s)", archive_path, content_hash)
            extracted_csv = extract_investigation_csv(archive_path)
            rows = _investigation_rows_from_csv(extracted_csv)
        else:
            rows = []

    if since_year is not None:
        filtered = []
        for row in rows:
            year_str = _field_getter(row)("YEAR", "year", "YEARTXT") or ""
            year_val = _parse_int(year_str)
            if year_val is not None and year_val >= since_year:
                filtered.append(row)
        rows = filtered

    if max_records is not None and len(rows) > max_records:
        logger.info("Capping harvest at %d records (raw rows available: %d)", max_records, len(rows))
        rows = rows[:max_records]

    normalize_investigation_row = _get_normalize_investigation_row()
    canonical_records: List[Dict[str, Any]] = []
    for sequence, row in enumerate(rows, start=1):
        canonical = normalize_investigation_row(source_id, sequence, row)
        canonical_records.append(canonical.model_dump())

    harvest_manifest = {
        "source_id": source_id,
        "source_family": "investigation",
        "source_url": url,
        "retrieval_method": "download_csv_zip",
        "harvest_timestamp": _utc_now(),
        "content_hash": content_hash,
        "record_count": len(canonical_records),
        "raw_archive_path": str(archive_path.relative_to(output_dir)) if not fixture_path else None,
        "extracted_csv_path": str(extracted_csv.relative_to(output_dir)) if extracted_csv else None,
        "fixture_path": str(fixture_path.name) if fixture_path else None,
        "since_year": since_year,
    }

    canonical_path = canonical_dir / "investigations.jsonl"
    manifest_path = raw_dir / "harvest_manifest.json"

    if not dry_run:
        tmp_canonical = canonical_path.with_suffix(".jsonl.tmp")
        with tmp_canonical.open("w", encoding="utf-8") as f:
            for record in canonical_records:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp_canonical, canonical_path)

        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_manifest.write_text(
            json.dumps(harvest_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_manifest, manifest_path)
    else:
        logger.info("Dry run: would write %d canonical records to %s", len(canonical_records), canonical_path)

    return {
        "harvest_manifest": harvest_manifest,
        "canonical_path": canonical_path if not dry_run else None,
        "record_count": len(canonical_records),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Harvest NHTSA automotive public data")
    parser.add_argument("--family", default="recalls", help="Source family to harvest (recalls|investigation)")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for harvested artifacts")
    parser.add_argument("--since-year", type=int, default=None, help="Filter records to this model year or later")
    parser.add_argument("--max-records", type=int, default=None, help="Cap the number of records processed")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count without writing artifacts")
    parser.add_argument("--fixture", type=Path, default=None, help="Replay from an authored fixture instead of network")
    parser.add_argument("--force-download", action="store_true", help="Re-download even if a cached archive exists")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.family == "recalls":
        harvest_func = harvest_recalls
    elif args.family == "investigation":
        harvest_func = harvest_investigations
    else:
        logger.error("Unsupported --family: %s (supported: recalls, investigation)", args.family)
        return 1

    try:
        result = harvest_func(
            output_dir=args.output_dir,
            since_year=args.since_year,
            max_records=args.max_records,
            dry_run=args.dry_run,
            fixture_path=args.fixture,
            force_download=args.force_download,
        )
    except HarvestError as exc:
        logger.error("%s: %s", exc.code, exc.message)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected harvest failure: %s", exc)
        return 1

    print(json.dumps(result["harvest_manifest"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
