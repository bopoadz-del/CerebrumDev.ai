"""JSON-file persistence for RAG ingestion source records and jobs.

Follows the same atomic write/replace pattern used by
``backend/app/core/session_persistence.py`` so that restarts do not lose
queued or validated ingestion jobs.

No documents, chunks, embeddings, or vector-store operations happen here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, List, Optional

from app.models.rag_ingestion import RagIngestionJob, RagSourceRecord

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_PATH = "./storage"


def _storage_path() -> str:
    """Return the current storage root, respecting runtime env changes."""
    return os.getenv("STORAGE_PATH", DEFAULT_STORAGE_PATH)


def _rag_ingestion_dir(domain: str) -> Path:
    """Return the per-domain storage directory, creating it if necessary."""
    path = Path(_storage_path()) / "rag_ingestion" / domain
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sources_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "records.json"


def _jobs_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "jobs.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* to *path* atomically, preserving a backup if it existed."""
    tmp_path = path.with_suffix(".json.tmp")
    backup_path = path.with_suffix(".json.bak")
    try:
        if path.exists():
            shutil.copy2(path, backup_path)
        tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _load_json(path: Path) -> dict:
    """Load JSON from *path*; return an empty dict if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}


def _model_map(model_cls: Any, items: List[dict]) -> List[Any]:
    """Instantiate model instances from raw dicts, skipping corrupt entries."""
    result = []
    for item in items:
        try:
            result.append(model_cls(**item))
        except Exception as exc:
            logger.warning("Skipping corrupt %s entry: %s", model_cls.__name__, exc)
    return result


def save_source_record(record: RagSourceRecord) -> RagSourceRecord:
    """Persist a source record, replacing any existing record with the same id."""
    path = _sources_path(record.domain)
    data = _load_json(path)
    records = data.get("records", [])
    records = [r for r in records if r.get("source_id") != record.source_id]
    records.append(record.model_dump(mode="json"))
    data["records"] = records
    _atomic_write(path, data)
    return record


def get_source_record(domain: str, source_id: str) -> Optional[RagSourceRecord]:
    """Fetch a single source record by domain and source id."""
    path = _sources_path(domain)
    data = _load_json(path)
    for item in data.get("records", []):
        if item.get("source_id") == source_id:
            return RagSourceRecord(**item)
    return None


def list_source_records(
    domain: str, collection_id: Optional[str] = None
) -> List[RagSourceRecord]:
    """List source records for a domain, optionally filtered by collection."""
    path = _sources_path(domain)
    data = _load_json(path)
    records = _model_map(RagSourceRecord, data.get("records", []))
    if collection_id:
        records = [r for r in records if r.collection_id == collection_id]
    return records


def save_job(job: RagIngestionJob) -> RagIngestionJob:
    """Persist an ingestion job, replacing any existing job with the same id."""
    path = _jobs_path(job.domain)
    data = _load_json(path)
    jobs = data.get("jobs", [])
    jobs = [j for j in jobs if j.get("job_id") != job.job_id]
    jobs.append(job.model_dump(mode="json"))
    data["jobs"] = jobs
    _atomic_write(path, data)
    return job


def get_job(domain: str, job_id: str) -> Optional[RagIngestionJob]:
    """Fetch a single ingestion job by domain and job id."""
    path = _jobs_path(domain)
    data = _load_json(path)
    for item in data.get("jobs", []):
        if item.get("job_id") == job_id:
            return RagIngestionJob(**item)
    return None


def list_jobs(
    domain: str,
    status: Optional[str] = None,
    rag_pack_id: Optional[str] = None,
    collection_id: Optional[str] = None,
) -> List[RagIngestionJob]:
    """List ingestion jobs for a domain, with optional filters."""
    path = _jobs_path(domain)
    data = _load_json(path)
    jobs = _model_map(RagIngestionJob, data.get("jobs", []))
    if status:
        jobs = [j for j in jobs if j.status.value == status]
    if rag_pack_id:
        jobs = [j for j in jobs if j.rag_pack_id == rag_pack_id]
    if collection_id:
        jobs = [j for j in jobs if j.collection_id == collection_id]
    return jobs
