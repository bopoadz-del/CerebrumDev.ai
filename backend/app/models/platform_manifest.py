"""Automotive (and future domain) platform manifest schema.

The manifest is the single source of truth consumed by the platform generator.
It describes domain identity, branding, required blocks, RAG packs, and feature
flags. Validation is strict: an invalid manifest must fail before generation
starts so a broken config cannot produce a broken platform package.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Branding and feature sub-models
# ---------------------------------------------------------------------------


class BrandingConfig(BaseModel):
    product_name: str = Field(..., min_length=1)
    workspace_name: str = Field(..., min_length=1)
    foundation_name: str = Field(..., min_length=1)


class GoogleDriveFeatureConfig(BaseModel):
    enabled: bool = True
    multi_user: bool = True
    personal_connections: bool = True
    organisation_shared_bindings: bool = True
    default_scope: Literal["read_only", "read_write"] = "read_only"


class FeaturesConfig(BaseModel):
    google_drive: GoogleDriveFeatureConfig = Field(
        default_factory=GoogleDriveFeatureConfig
    )


# ---------------------------------------------------------------------------
# Main manifest
# ---------------------------------------------------------------------------


KNOWN_DOMAIN_KITS: set[str] = {"automotive"}
KNOWN_DOMAIN_BLOCKS: set[str] = {"automotive_v2"}
KNOWN_FORMULA_BLOCKS: set[str] = {"formula_executor_v2"}
VALID_REFERENCE_IDENTIFIERS: set[str] = {
    "nhtsa_campaign_number",
    "odi_number",
    "investigation_number",
    "make",
    "model",
    "model_year",
    "component",
    "manufacturer",
}
VALID_COLLECTION_STRATEGIES: set[str] = {"project_scoped"}


class AutomotivePlatformManifest(BaseModel):
    """Canonical generated-platform manifest.

    Example:
        {
          "schema_version": "1.0.0",
          "platform_id": "automotive-safety-intelligence",
          "platform_name": "Automotive Safety Intelligence",
          "domain": "automotive",
          "domain_kit": "automotive",
          "domain_block": "automotive_v2",
          "formula_block": "formula_executor_v2",
          "foundation_rag_pack": "automotive_core_rag_v1",
          "foundation_collection": "automotive_core_v1",
          "client_overlay_enabled": true,
          "client_collection_strategy": "project_scoped",
          "reference_identifiers": [...],
          "branding": {...},
          "features": {...}
        }
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    platform_id: str = Field(..., min_length=1, pattern=r"^[a-z0-9-]+$")
    platform_name: str = Field(..., min_length=1)
    domain: str = Field(..., min_length=1)
    domain_kit: str = Field(..., min_length=1)
    domain_block: str = Field(..., min_length=1)
    formula_block: str = Field(..., min_length=1)
    foundation_rag_pack: str = Field(..., min_length=1)
    foundation_collection: str = Field(..., min_length=1)
    client_overlay_enabled: bool = True
    client_collection_strategy: Literal["project_scoped"] = "project_scoped"
    reference_identifiers: list[str] = Field(default_factory=list)
    branding: BrandingConfig
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)

    @field_validator("reference_identifiers")
    @classmethod
    def _validate_reference_identifiers(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one reference identifier is required")
        unknown = set(value) - VALID_REFERENCE_IDENTIFIERS
        if unknown:
            raise ValueError(f"unknown reference identifiers: {sorted(unknown)}")
        duplicates = {v for v in value if value.count(v) > 1}
        if duplicates:
            raise ValueError(f"duplicate reference identifiers: {sorted(duplicates)}")
        return value

    @model_validator(mode="after")
    def _validate_blocks(self) -> "AutomotivePlatformManifest":
        if self.domain_kit not in KNOWN_DOMAIN_KITS:
            raise ValueError(
                f"domain_kit '{self.domain_kit}' is not known; "
                f"expected one of {sorted(KNOWN_DOMAIN_KITS)}"
            )
        if self.domain_block not in KNOWN_DOMAIN_BLOCKS:
            raise ValueError(
                f"domain_block '{self.domain_block}' is not known; "
                f"expected one of {sorted(KNOWN_DOMAIN_BLOCKS)}"
            )
        if self.formula_block not in KNOWN_FORMULA_BLOCKS:
            raise ValueError(
                f"formula_block '{self.formula_block}' is not known; "
                f"expected one of {sorted(KNOWN_FORMULA_BLOCKS)}"
            )
        return self


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def parse_manifest(data: dict[str, Any]) -> AutomotivePlatformManifest:
    """Parse and validate a manifest dict. Raises ValueError on failure."""
    try:
        return AutomotivePlatformManifest.model_validate(data)
    except Exception as exc:
        raise ValueError(f"invalid platform manifest: {exc}") from exc


def validate_manifest(manifest: AutomotivePlatformManifest) -> None:
    """Re-validate an already-parsed manifest.

    Pydantic models are validated on construction, so this is mainly a hook for
    tests and callers that want an explicit validation step.
    """
    # model_validate on model_dump() exercises all validators again.
    AutomotivePlatformManifest.model_validate(manifest.model_dump())
