"""Pydantic models for RAG ingestion source records and jobs.

This module defines the persistent metadata layer that sits between RAG pack
metadata and future document ingestion. No documents, chunks, embeddings, or
vector-store writes happen here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class SourceClass(str, Enum):
    """Source class values that may appear in a source record.

    A RAG pack's ``source_policy`` determines which of these are allowed or
    precluded for that pack.
    """

    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    OFFICIAL_STATUTE_OR_REGULATION = "official_statute_or_regulation"
    OFFICIAL_GUIDANCE = "official_guidance"
    PLATFORM_CURATED_TEMPLATE = "platform_curated_template"
    PRIVATE_ENTERPRISE_DATA = "private_enterprise_data"
    CONFIDENTIAL_CLIENT_DATA = "confidential_client_data"
    COPYRIGHTED_COMMERCIAL_CONTENT_WITHOUT_LICENSE = "copyrighted_commercial_content_without_license"
    USER_UPLOADED_PROJECT_RECORDS = "user_uploaded_project_records"
    UNKNOWN_LICENSE = "unknown_license"


class JobStatus(str, Enum):
    """Lifecycle status for an ingestion job."""

    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"
    QUEUED = "queued"
    INGESTING = "ingesting"
    INDEXED = "indexed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DuplicateStatus(str, Enum):
    """Result of a document-level duplicate check."""

    NOT_CHECKED = "not_checked"
    UNIQUE = "unique"
    DUPLICATE = "duplicate"


class ValidationError(BaseModel):
    """Machine-readable validation error returned by the ingestion validator."""

    code: str
    field: Optional[str] = None
    message: str


class RagSourceRecord(BaseModel):
    """A proposed source document to be ingested into a RAG collection."""

    source_id: str = Field(default_factory=lambda: str(uuid4()))
    rag_pack_id: str
    collection_id: str
    domain: str
    source_class: SourceClass
    title: str
    source_uri: str
    publisher: Optional[str] = None
    license_name: Optional[str] = None
    license_uri: Optional[str] = None
    license_review_status: Optional[str] = None
    authority_rating: Optional[str] = None
    content_hash: Optional[str] = None
    external_document_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RagIngestionJob(BaseModel):
    """An auditable ingestion job linking a source record to a RAG pack."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    rag_pack_id: str
    collection_id: str
    domain: str
    source_id: str
    status: JobStatus = JobStatus.DRAFT
    dry_run: bool = True
    duplicate_status: DuplicateStatus = DuplicateStatus.NOT_CHECKED
    validation_errors: List[ValidationError] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None


class AcquisitionStatus(str, Enum):
    """Lifecycle status for an acquisition attempt."""

    PENDING = "pending"
    FETCHING = "fetching"
    FETCHED = "fetched"
    DUPLICATE = "duplicate"
    PARSED = "parsed"
    FAILED = "failed"


class ParseStatus(str, Enum):
    """Parsing outcome recorded in an acquisition report."""

    NOT_REQUESTED = "not_requested"
    NOT_STARTED = "not_started"
    PARSED = "parsed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class RagAcquisitionReport(BaseModel):
    """Audit report for a safe source acquisition and parsing preview.

    No raw source bytes or full extracted text are stored here — only bounded
    metadata, a preview, warnings, and structured errors.
    """

    acquisition_id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str
    source_id: str
    rag_pack_id: str
    collection_id: str
    domain: str
    source_uri: str
    final_source_uri: Optional[str] = None
    status: AcquisitionStatus = AcquisitionStatus.PENDING
    http_status: Optional[int] = None
    redirect_count: int = 0
    content_type: Optional[str] = None
    content_length_bytes: Optional[int] = None
    computed_content_hash: Optional[str] = None
    duplicate_status: DuplicateStatus = DuplicateStatus.NOT_CHECKED
    duplicate_match_source_id: Optional[str] = None
    parser_id: Optional[str] = None
    parser_version: Optional[str] = None
    parse_status: ParseStatus = ParseStatus.NOT_REQUESTED
    page_count: Optional[int] = None
    extracted_characters: int = 0
    text_preview: str = ""
    warnings: List[str] = Field(default_factory=list)
    errors: List[ValidationError] = Field(default_factory=list)
    raw_artifact_persisted: bool = False
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
