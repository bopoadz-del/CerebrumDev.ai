"""SQLAlchemy ORM models for Steward production RAG + structured packs."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.steward.config import EMBED_DIM


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EstateProject(Base):
    """Tenant-scoped project: platform Layer-1 or an estate (Layer-2)."""

    __tablename__ = "estate_projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical_name", name="uq_estate_project_canonical"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255))
    knowledge_layer: Mapped[int] = mapped_column(Integer, default=2)  # 1=platform, 2=estate
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SourceRecord(Base):
    """Governed acquisition / licence provenance for packs and uploads."""

    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "rag_pack_id", "source_id", name="uq_source_release_pack"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    rag_pack_id: Mapped[str] = mapped_column(String(128), index=True)
    collection_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    publisher: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_class: Mapped[str] = mapped_column(String(64), default="platform_curated")
    authority_level: Mapped[str] = mapped_column(String(64), default="advisory")
    licence: Mapped[str] = mapped_column(String(128))
    original_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    resolved_download_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    content_checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    profile: Mapped[str] = mapped_column(String(16), default="mini")  # mini|full
    knowledge_layer: Mapped[int] = mapped_column(Integer, default=1)
    authority_kind: Mapped[str] = mapped_column(
        String(64), default="advisory"
    )  # authoritative|advisory|synthetic|evaluation_only|estate_owned
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("estate_projects.id"), index=True)
    knowledge_layer: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    source_uri: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    rag_pack_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    collection_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_record_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authority_kind: Mapped[str] = mapped_column(String(64), default="estate_owned")
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    chunks: Mapped[List["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    project_id: Mapped[str] = mapped_column(index=True)
    knowledge_layer: Mapped[int] = mapped_column(Integer, index=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM))
    text_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
    )
    source_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sheet: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    row_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    row_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rag_pack_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    authority_kind: Mapped[str] = mapped_column(String(64), default="estate_owned")
    cite_as_policy: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    document: Mapped["Document"] = relationship(back_populates="chunks")


class HospitalityIntent(Base):
    """Non-authoritative routing/evaluation intents — never cite as policy."""

    __tablename__ = "hospitality_intents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    project_id: Mapped[str] = mapped_column(index=True)
    intent_label: Mapped[str] = mapped_column(String(128), index=True)
    utterance: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), default="en")
    authority_kind: Mapped[str] = mapped_column(String(64), default="synthetic")
    cite_as_policy: Mapped[bool] = mapped_column(Boolean, default=False)
    source_record_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FacilityAsset(Base):
    """Structured facilities rows — NOT embedded into pgvector."""

    __tablename__ = "facility_assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    estate_id: Mapped[str] = mapped_column(index=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    building: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    floor: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    room_zone: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    asset_label: Mapped[str] = mapped_column(String(255))
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    serial: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    maintenance_interval_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    work_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="active")
    cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4), nullable=True)
    labour_hours: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4), nullable=True)
    inspection_result: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    evaluation_only: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FleetVehicle(Base):
    """Structured fleet rows — NOT embedded into pgvector."""

    __tablename__ = "fleet_vehicles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    estate_id: Mapped[str] = mapped_column(index=True)
    vin_or_internal_id: Mapped[str] = mapped_column(String(64), index=True)
    registration: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    make: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    driver_assignment: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    odometer_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    next_service_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    next_service_due: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    defect_severity: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(64), default="available")
    evaluation_only: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
