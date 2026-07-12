"""Tests for automotive foundation retrieval."""

from __future__ import annotations

import pytest

from app.core.automotive_normalizers import normalize_recall_rows
from app.core.automotive_pack_builder import build_automotive_core_pack
from app.core.automotive_retrieval import (
    FOUNDATION_PROJECT_ID,
    retrieve_by_campaign_number,
    retrieve_foundation_evidence,
)


def _index_sample_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.core.rag.embeddings import reset_embedder_cache
    from app.core.rag.vector_store import reset_store_cache

    reset_embedder_cache()
    reset_store_cache()

    rows = [
        {
            "CAMPNO": "15V176000",
            "MAKETXT": "Honda",
            "MODELTXT": "Accord",
            "YEARTXT": "2014",
            "MFGNAME": "Honda",
            "COMPNAME": "AIR BAGS",
            "RCDATE": "20150331",
            "POTAFF": "1900000",
            "DESC_DEFECT": "Passenger air bag moisture intrusion.",
            "CONEQUENCE_DEFECT": "Air bag may not deploy.",
            "CORRECTIVE_ACTION": "Replace air bag.",
        },
        {
            "CAMPNO": "21V127000",
            "MAKETXT": "Toyota",
            "MODELTXT": "Camry",
            "YEARTXT": "2018",
            "MFGNAME": "Toyota",
            "COMPNAME": "FUEL PUMP",
            "RCDATE": "20210225",
            "POTAFF": "152000",
            "DESC_DEFECT": "Low-pressure fuel pump may fail.",
            "CONEQUENCE_DEFECT": "Engine may stall.",
            "CORRECTIVE_ACTION": "Replace fuel pump.",
        },
    ]
    records = normalize_recall_rows("nhtsa_recalls", rows)
    canonical_path = tmp_path / "recalls.jsonl"
    with canonical_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    build_automotive_core_pack(
        canonical_records_path=canonical_path,
        output_dir=tmp_path / "pack",
        project_id=FOUNDATION_PROJECT_ID,
        dry_run=False,
    )


def test_exact_campaign_lookup_returns_top_result(tmp_path, monkeypatch) -> None:
    _index_sample_records(tmp_path, monkeypatch)
    results = retrieve_by_campaign_number("15V176000")
    assert len(results) >= 1
    assert results[0].record_reference == "15V176000"
    assert results[0].knowledge_layer == FOUNDATION_PROJECT_ID
    assert results[0].source_family == "recall"
    assert results[0].retrieval_score > 0


def test_vehicle_lookup_returns_relevant_evidence(tmp_path, monkeypatch) -> None:
    _index_sample_records(tmp_path, monkeypatch)
    results = retrieve_foundation_evidence("2018 Toyota Camry fuel pump recall", top_k=5)
    references = {r.record_reference for r in results}
    assert "21V127000" in references


def test_component_query_returns_relevant_evidence(tmp_path, monkeypatch) -> None:
    _index_sample_records(tmp_path, monkeypatch)
    results = retrieve_foundation_evidence("air bag moisture problem Honda", top_k=5)
    references = {r.record_reference for r in results}
    assert "15V176000" in references


def test_unsupported_campaign_does_not_fabricate_match(tmp_path, monkeypatch) -> None:
    _index_sample_records(tmp_path, monkeypatch)
    results = retrieve_by_campaign_number("99V999000")
    # The identifier search may return nothing; semantic search should not invent.
    if results:
        assert all(r.record_reference != "99V999000" for r in results)


def test_citation_envelope_is_complete(tmp_path, monkeypatch) -> None:
    _index_sample_records(tmp_path, monkeypatch)
    results = retrieve_foundation_evidence("Honda Accord 2014", top_k=5)
    assert results
    for r in results:
        assert r.knowledge_layer == FOUNDATION_PROJECT_ID
        assert r.foundation_pack_id == "automotive_core_rag_v1"
        assert r.source_family == "recall"
        assert r.source_authority == "primary"
        assert r.record_reference
        assert r.chunk_text
        assert "metadata" in r.__dict__ or hasattr(r, "metadata")
