"""Canonical automotive record models for the generated platform.

These models describe official public-data records after normalization.
They are deliberately free of construction terminology and include enough
provenance to trace every record back to the harvested artifact.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class AutomotiveRecall(BaseModel):
    """Canonical NHTSA recall campaign record."""

    record_id: str = Field(..., description="Deterministic record identity")
    source_id: str = Field(..., description="Official source identifier")
    source_family: Literal["recall"] = "recall"
    campaign_number: str = Field(..., description="NHTSA campaign number, e.g. 15V176000")
    manufacturer: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None
    component: Optional[str] = None
    summary: Optional[str] = None
    consequence: Optional[str] = None
    remedy: Optional[str] = None
    report_received_date: Optional[str] = None
    affected_units: Optional[int] = None
    source_url: Optional[str] = None
    jurisdiction: str = "US"
    authority_rating: str = "primary"
    harvest_timestamp: str
    raw_record_hash: str
    normalization_version: str = "automotive_core_v1"


class AutomotiveInvestigation(BaseModel):
    """Canonical NHTSA ODI defect investigation record."""

    record_id: str = Field(..., description="Deterministic record identity")
    source_id: str = Field(..., description="Official source identifier")
    source_family: Literal["investigation"] = "investigation"
    investigation_number: str = Field(..., description="NHTSA action number, e.g. PE16-007")
    status: Optional[str] = None
    investigation_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None
    model_year_range: Optional[str] = None
    component: Optional[str] = None
    manufacturer: Optional[str] = None
    subject: Optional[str] = None
    summary: Optional[str] = None
    opening_date: Optional[str] = None
    closing_date: Optional[str] = None
    associated_campaign_number: Optional[str] = None
    source_url: Optional[str] = None
    jurisdiction: str = "US"
    authority_rating: str = "primary"
    harvest_timestamp: str
    raw_record_hash: str
    normalization_version: str = "automotive_core_v1"


class AutomotiveComplaint(BaseModel):
    """Canonical NHTSA consumer complaint record."""

    record_id: str = Field(..., description="Deterministic record identity")
    source_id: str = Field(..., description="Official source identifier")
    source_family: Literal["complaint"] = "complaint"
    complaint_id: str = Field(..., description="NHTSA complaint / ODI number")
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None
    component: Optional[str] = None
    manufacturer: Optional[str] = None
    summary: Optional[str] = None
    crash: Optional[bool] = None
    fire: Optional[bool] = None
    injured: Optional[int] = None
    deaths: Optional[int] = None
    date_complaint: Optional[str] = None
    associated_campaign_number: Optional[str] = None
    source_url: Optional[str] = None
    jurisdiction: str = "US"
    authority_rating: str = "primary"
    harvest_timestamp: str
    raw_record_hash: str
    normalization_version: str = "automotive_core_v1"


class AutomotiveSafetyRating(BaseModel):
    """Canonical NHTSA vehicle safety rating record."""

    record_id: str = Field(..., description="Deterministic record identity")
    source_id: str = Field(..., description="Official source identifier")
    source_family: Literal["safety_rating"] = "safety_rating"
    vehicle_id: str = Field(..., description="NHTSA vehicle identifier")
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None
    overall_rating: Optional[str] = None
    front_crash_rating: Optional[str] = None
    side_crash_rating: Optional[str] = None
    rollover_rating: Optional[str] = None
    vehicle_description: Optional[str] = None
    source_url: Optional[str] = None
    jurisdiction: str = "US"
    authority_rating: str = "primary"
    harvest_timestamp: str
    raw_record_hash: str
    normalization_version: str = "automotive_core_v1"


class AutomotiveChunk(BaseModel):
    """One deterministic retrieval chunk produced from a canonical record."""

    chunk_id: str = Field(..., description="Deterministic chunk identity")
    record_id: str
    source_id: str
    source_family: str
    campaign_number: str = ""
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None
    component: Optional[str] = None
    knowledge_layer: str = "automotive_core_v1"
    foundation_pack_id: str = "automotive_core_rag_v1"
    source_authority: str = "primary"
    jurisdiction: str = "US"
    chunk_index: int
    chunking_version: str = "v2"
    text: str
    text_hash: str
    record_reference: str
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PackManifest(BaseModel):
    """Versioned manifest describing a built automotive foundation pack."""

    pack_id: str = "automotive_core_rag_v1"
    pack_version: str = "automotive_core_rag_v1.0.0"
    foundation_collection: str = "automotive_core_v1"
    harvest_timestamp: str
    build_timestamp: str
    record_count: int
    chunk_count: int
    embedding_identity: Dict[str, Any]
    source_families: list[str]
    status: Literal["validated", "indexed", "activated"] = "validated"
