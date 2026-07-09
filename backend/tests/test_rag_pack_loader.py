"""Tests for the Cerebrum-Blocks RAG pack loader (read-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.rag_pack_loader import (
    RagPackLoaderError,
    get_rag_pack,
    list_rag_pack_domain_ids,
    list_rag_packs,
    load_rag_packs,
)


@pytest.fixture
def fake_engine_with_rag_packs(tmp_path: Path) -> Path:
    """Build a minimal fake engine checkout containing a RAG pack shelf."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_dir = engine_root / "block_store" / "shelves"
    shelf_dir.mkdir(parents=True)

    shelf = {
        "schema_version": "1.0.0",
        "shelf_id": "rag_packs",
        "name": "Test RAG Pack Shelf",
        "description": "test",
        "packs": [
            {
                "id": "legal_core_rag",
                "domain": "legal",
                "name": "Legal Core RAG Pack",
                "description": "contract review knowledge",
                "collection_id": "prebuilt_legal_core",
                "visibility": "platform_prebuilt",
                "data_class": "public_or_licensed_reference",
                "enterprise_specific": False,
                "requires_blocks": ["knowledge", "vector_search"],
                "recommended_with_blocks": ["legal_v2", "formula_executor_v2"],
                "source_types": ["statutes", "templates"],
                "expected_queries": ["what is the governing law?"],
                "expected_outputs": ["cited answer"],
                "fetch_mode": "metadata_only",
                "ingestion_status": "not_ingested",
                "notes": [],
            },
            {
                "id": "finance_core_rag",
                "domain": "finance",
                "name": "Finance Core RAG Pack",
                "description": "financial knowledge",
                "collection_id": "prebuilt_finance_core",
                "visibility": "platform_prebuilt",
                "data_class": "public_or_licensed_reference",
                "enterprise_specific": False,
                "requires_blocks": ["knowledge", "vector_search"],
                "recommended_with_blocks": ["finance_v2", "formula_executor_v2"],
                "source_types": ["regulations", "guidance"],
                "expected_queries": ["summarize this filing"],
                "expected_outputs": ["cited answer"],
                "fetch_mode": "metadata_only",
                "ingestion_status": "not_ingested",
                "notes": [],
            },
        ],
    }
    (shelf_dir / "rag_packs.json").write_text(json.dumps(shelf), encoding="utf-8")
    return engine_root


def test_load_rag_packs(fake_engine_with_rag_packs: Path):
    data = load_rag_packs(fake_engine_with_rag_packs)
    assert data["shelf_id"] == "rag_packs"
    assert len(data["packs"]) == 2


def test_load_rag_packs_missing_file(tmp_path: Path):
    with pytest.raises(RagPackLoaderError):
        load_rag_packs(tmp_path)


def test_load_rag_packs_invalid_json(tmp_path: Path):
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_path = engine_root / "block_store" / "shelves" / "rag_packs.json"
    shelf_path.parent.mkdir(parents=True)
    shelf_path.write_text("not json", encoding="utf-8")
    with pytest.raises(RagPackLoaderError):
        load_rag_packs(engine_root)


def test_list_rag_packs(fake_engine_with_rag_packs: Path):
    packs = list_rag_packs(fake_engine_with_rag_packs)
    assert len(packs) == 2
    domains = {p["domain"] for p in packs}
    assert domains == {"legal", "finance"}
    for p in packs:
        assert "collection_id" in p
        assert "requires_blocks" in p
        assert "recommended_with_blocks" in p
        assert "fetch_mode" in p
        assert "ingestion_status" in p
        assert "enterprise_specific" in p


def test_list_rag_pack_domain_ids(fake_engine_with_rag_packs: Path):
    assert list_rag_pack_domain_ids(fake_engine_with_rag_packs) == ["finance", "legal"]


def test_get_rag_pack(fake_engine_with_rag_packs: Path):
    legal = get_rag_pack("legal", fake_engine_with_rag_packs)
    assert legal is not None
    assert legal["id"] == "legal_core_rag"
    assert "knowledge" in legal["requires_blocks"]
    assert "legal_v2" in legal["recommended_with_blocks"]
    assert get_rag_pack("missing", fake_engine_with_rag_packs) is None
