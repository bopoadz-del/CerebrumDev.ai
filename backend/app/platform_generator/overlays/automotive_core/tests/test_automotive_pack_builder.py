"""Tests for the automotive foundation pack builder."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.automotive_normalizers import normalize_recall_rows
from app.core.automotive_pack_builder import (
    FOUNDATION_COLLECTION,
    FOUNDATION_PACK_ID,
    build_automotive_core_pack,
    build_automotive_core_pack_from_families,
    chunk_investigation_records,
    chunk_recall_records,
    load_canonical_records,
)
from app.models.automotive_records import AutomotiveInvestigation, AutomotiveRecall


def _sample_records() -> list[AutomotiveRecall]:
    rows = [
        {
            "CAMPNO": "15V176000",
            "MAKETXT": "Honda",
            "MODELTXT": "Accord",
            "YEARTXT": "2014",
            "MFGNAME": "Honda (American Honda Motor Co.)",
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
            "MFGNAME": "Toyota Motor Engineering & Manufacturing",
            "COMPNAME": "FUEL PUMP",
            "RCDATE": "20210225",
            "POTAFF": "152000",
            "DESC_DEFECT": "Low-pressure fuel pump may fail.",
            "CONEQUENCE_DEFECT": "Engine may stall.",
            "CORRECTIVE_ACTION": "Replace fuel pump.",
        },
    ]
    return normalize_recall_rows("nhtsa_recalls", rows)


def test_chunk_investigation_records_are_deterministic() -> None:
    records = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            component="AIR BAGS",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    chunks = chunk_investigation_records(records)
    assert len(chunks) == 1
    assert chunks[0].record_reference == "PE16-007"
    assert chunks[0].metadata["investigation_number"] == "PE16-007"


def test_chunk_recall_records_are_deterministic() -> None:
    records = _sample_records()
    chunks = chunk_recall_records(records)
    assert len(chunks) == 2
    first_ids = [c.chunk_id for c in chunks]
    second = chunk_recall_records(records)
    assert [c.chunk_id for c in second] == first_ids
    assert all(c.campaign_number for c in chunks)
    assert all(c.knowledge_layer == FOUNDATION_COLLECTION for c in chunks)
    assert all(c.foundation_pack_id == FOUNDATION_PACK_ID for c in chunks)


def test_chunk_text_includes_campaign_and_vehicle() -> None:
    records = _sample_records()
    chunks = chunk_recall_records(records)
    honda = next(c for c in chunks if c.make == "Honda")
    assert honda.campaign_number in honda.text
    assert "Honda" in honda.text
    assert "Accord" in honda.text
    assert "2014" in honda.text
    assert "AIR BAGS" in honda.text


def test_build_pack_dry_run_writes_chunks_and_manifest(tmp_path: Path) -> None:
    records = _sample_records()
    canonical_path = tmp_path / "recalls.jsonl"
    with canonical_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    manifest = build_automotive_core_pack(
        canonical_records_path=canonical_path,
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=True,
    )

    assert manifest.record_count == 2
    assert manifest.chunk_count == 2
    assert manifest.status == "validated"
    assert manifest.embedding_identity["model"] == "fake"

    chunks_path = tmp_path / "pack" / "chunks" / "recalls.jsonl"
    assert chunks_path.exists()
    lines = chunks_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    manifest_path = tmp_path / "pack" / "pack_manifest.json"
    assert manifest_path.exists()


def test_build_pack_with_fake_embedder_indexes(tmp_path: Path, monkeypatch) -> None:
    """Exercise the full embedding + indexing path with the fake embedder."""
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.core.rag.embeddings import reset_embedder_cache
    from app.core.rag.vector_store import reset_store_cache

    reset_embedder_cache()
    reset_store_cache()

    records = _sample_records()
    canonical_path = tmp_path / "recalls.jsonl"
    with canonical_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    manifest = build_automotive_core_pack(
        canonical_records_path=canonical_path,
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=False,
    )

    assert manifest.status == "indexed"
    assert manifest.chunk_count == 2
    assert manifest.embedding_identity["dim"] == 256


def test_rebuild_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.core.rag.embeddings import reset_embedder_cache
    from app.core.rag.vector_store import reset_store_cache

    reset_embedder_cache()
    reset_store_cache()

    records = _sample_records()
    canonical_path = tmp_path / "recalls.jsonl"
    with canonical_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    first = build_automotive_core_pack(
        canonical_records_path=canonical_path,
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=True,
    )
    second = build_automotive_core_pack(
        canonical_records_path=canonical_path,
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=True,
    )
    assert first.chunk_count == second.chunk_count
    assert first.record_count == second.record_count


def test_build_pack_from_families_dry_run_writes_both_chunk_files(tmp_path: Path) -> None:
    recalls = _sample_records()
    recalls_path = tmp_path / "recalls.jsonl"
    with recalls_path.open("w", encoding="utf-8") as f:
        for r in recalls:
            f.write(r.model_dump_json() + "\n")

    investigations = [
        AutomotiveInvestigation(
            record_id="i1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            component="AIR BAGS",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    investigations_path = tmp_path / "investigations.jsonl"
    with investigations_path.open("w", encoding="utf-8") as f:
        for r in investigations:
            f.write(r.model_dump_json() + "\n")

    manifest = build_automotive_core_pack_from_families(
        canonical_records_paths=[recalls_path, investigations_path],
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=True,
    )

    assert manifest.record_count == 3
    assert manifest.chunk_count == 3
    assert manifest.status == "validated"
    assert set(manifest.source_families) == {"recall", "investigation"}

    recalls_chunks_path = tmp_path / "pack" / "chunks" / "recalls.jsonl"
    investigations_chunks_path = tmp_path / "pack" / "chunks" / "investigations.jsonl"
    assert recalls_chunks_path.exists()
    assert investigations_chunks_path.exists()
    assert len(recalls_chunks_path.read_text(encoding="utf-8").strip().splitlines()) == 2
    assert len(investigations_chunks_path.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_cli_rejects_missing_records(tmp_path: Path) -> None:
    from scripts.build_automotive_core_pack import main

    missing = tmp_path / "missing.jsonl"
    result = main([
        "--records", str(missing),
        "--output", str(tmp_path / "pack"),
    ])
    assert result == 1


def test_cli_accepts_multiple_records(tmp_path: Path) -> None:
    from scripts.build_automotive_core_pack import main

    recalls = _sample_records()
    recalls_path = tmp_path / "recalls.jsonl"
    with recalls_path.open("w", encoding="utf-8") as f:
        for r in recalls:
            f.write(r.model_dump_json() + "\n")

    investigations = [
        AutomotiveInvestigation(
            record_id="i1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            component="AIR BAGS",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    investigations_path = tmp_path / "investigations.jsonl"
    with investigations_path.open("w", encoding="utf-8") as f:
        for r in investigations:
            f.write(r.model_dump_json() + "\n")

    result = main([
        "--records", str(recalls_path),
        "--records", str(investigations_path),
        "--output", str(tmp_path / "pack"),
        "--dry-run",
    ])
    assert result == 0
    manifest_path = tmp_path / "pack" / "pack_manifest.json"
    assert manifest_path.exists()
