"""Tests for the generated-platform manifest schema."""

import pytest

from app.models.platform_manifest import (
    parse_manifest,
    validate_manifest,
)


@pytest.fixture
def valid_manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "platform_id": "automotive-safety-intelligence",
        "platform_name": "Automotive Safety Intelligence",
        "domain": "automotive",
        "domain_kit": "automotive",
        "domain_block": "automotive_v2",
        "formula_block": "formula_executor_v2",
        "foundation_rag_pack": "automotive_core_rag_v1",
        "foundation_collection": "automotive_core_v1",
        "client_overlay_enabled": True,
        "client_collection_strategy": "project_scoped",
        "reference_identifiers": [
            "nhtsa_campaign_number",
            "odi_number",
            "investigation_number",
            "make",
            "model",
            "model_year",
            "component",
            "manufacturer",
        ],
        "branding": {
            "product_name": "Automotive Safety Intelligence",
            "workspace_name": "Vehicle Safety Workspace",
            "foundation_name": "Automotive Core Knowledge",
        },
        "features": {
            "google_drive": {
                "enabled": True,
                "multi_user": True,
                "personal_connections": True,
                "organisation_shared_bindings": True,
                "default_scope": "read_only",
            }
        },
    }


def test_parse_valid_manifest(valid_manifest: dict) -> None:
    manifest = parse_manifest(valid_manifest)
    assert manifest.platform_id == "automotive-safety-intelligence"
    assert manifest.branding.product_name == "Automotive Safety Intelligence"


def test_validate_manifest(valid_manifest: dict) -> None:
    manifest = parse_manifest(valid_manifest)
    validate_manifest(manifest)  # should not raise


def test_missing_reference_identifiers(valid_manifest: dict) -> None:
    valid_manifest["reference_identifiers"] = []
    with pytest.raises(ValueError, match="at least one reference identifier"):
        parse_manifest(valid_manifest)


def test_unknown_reference_identifier(valid_manifest: dict) -> None:
    valid_manifest["reference_identifiers"].append("unknown_id")
    with pytest.raises(ValueError, match="unknown reference identifiers"):
        parse_manifest(valid_manifest)


def test_duplicate_reference_identifier(valid_manifest: dict) -> None:
    valid_manifest["reference_identifiers"].append("make")
    with pytest.raises(ValueError, match="duplicate reference identifiers"):
        parse_manifest(valid_manifest)


def test_unknown_domain_kit(valid_manifest: dict) -> None:
    valid_manifest["domain_kit"] = "legal"
    with pytest.raises(ValueError, match="domain_kit 'legal' is not known"):
        parse_manifest(valid_manifest)


def test_unknown_domain_block(valid_manifest: dict) -> None:
    valid_manifest["domain_block"] = "legal_v1"
    with pytest.raises(ValueError, match="domain_block 'legal_v1' is not known"):
        parse_manifest(valid_manifest)


def test_unknown_formula_block(valid_manifest: dict) -> None:
    valid_manifest["formula_block"] = "formula_v3"
    with pytest.raises(ValueError, match="formula_block 'formula_v3' is not known"):
        parse_manifest(valid_manifest)


def test_missing_branding(valid_manifest: dict) -> None:
    del valid_manifest["branding"]
    with pytest.raises(ValueError):
        parse_manifest(valid_manifest)


def test_fixture_file_is_valid() -> None:
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).parent.parent.parent
        / "docs"
        / "superpowers"
        / "fixtures"
        / "automotive_platform_manifest.json"
    )
    data = json.loads(fixture.read_text(encoding="utf-8"))
    manifest = parse_manifest(data)
    assert manifest.platform_id == "automotive-safety-intelligence"
