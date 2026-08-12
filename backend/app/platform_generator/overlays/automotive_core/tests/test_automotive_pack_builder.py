"""Tests for the automotive foundation pack builder."""

from __future__ import annotations

from pathlib import Path


from app.core.automotive_normalizers import normalize_recall_rows
from app.core.automotive_pack_builder import (
    FOUNDATION_COLLECTION,
    FOUNDATION_PACK_ID,
    _bulk_index_chunks,
    build_automotive_core_pack,
    build_automotive_core_pack_from_families,
    chunk_investigation_records,
    chunk_recall_records,
)
from app.models.automotive_records import AutomotiveChunk, AutomotiveInvestigation, AutomotiveRecall


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
    # AutomotiveChunk has no top-level investigation_number field; the
    # investigation number is surfaced via record_reference and metadata so
    # downstream retrieval/citation can identify the source record.
    assert chunks[0].record_reference == "PE16-007"
    assert chunks[0].metadata["investigation_number"] == "PE16-007"


def test_bulk_index_chunks_encodes_source_family_in_doc_id() -> None:
    """_bulk_index_chunks prefixes doc_id with source_family for retrieval.

    The RAG modules (app.core.rag.vector_store / app.core.models) are not part
    of this overlay, so we mock the store boundary and assert on the rows that
    would be written to the chunks table.
    """
    from unittest.mock import MagicMock, patch

    from sqlalchemy import Column, Integer, MetaData, String, Table

    recall_chunk = AutomotiveChunk(
        chunk_id="r1-0",
        record_id="r1",
        source_id="nhtsa_recalls",
        source_family="recall",
        campaign_number="15V176000",
        make="Honda",
        model="Accord",
        model_year="2014",
        component="AIR BAGS",
        chunk_index=0,
        text="Recall text",
        text_hash="hash1",
        record_reference="15V176000",
    )
    investigation_chunk = AutomotiveChunk(
        chunk_id="i1-0",
        record_id="i1",
        source_id="nhtsa_investigations",
        source_family="investigation",
        campaign_number="",
        make="Tesla",
        model="Model S",
        model_year="2015",
        component="AIR BAGS",
        chunk_index=0,
        text="Investigation text",
        text_hash="hash2",
        record_reference="PE16-007",
        metadata={"investigation_number": "PE16-007"},
    )

    # Provide a minimal table that can compile the INSERT statements generated
    # by _bulk_index_chunks.
    metadata = MetaData()
    mock_table = Table(
        "chunks",
        metadata,
        Column("chunk_id", String),
        Column("project_id", String),
        Column("doc_id", String),
        Column("chunk_index", Integer),
        Column("text", String),
        Column("embedding", String),
        Column("created_at", String),
        Column("embedding_model", String),
        Column("embedding_dim", Integer),
        Column("embedding_normalized", Integer),
    )

    mock_session = MagicMock()
    mock_session.bind.dialect.name = "other"  # forces per-row generic INSERT path
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_session_factory = MagicMock(return_value=mock_session)
    mock_store = MagicMock()
    mock_store._rag_chunk_cls.__table__ = mock_table
    mock_store._session_factory = MagicMock(return_value=mock_session_factory)
    mock_store._lock = MagicMock()
    mock_store._lock.__enter__ = MagicMock(return_value=mock_store)
    mock_store._lock.__exit__ = MagicMock(return_value=False)

    fake_models = MagicMock()
    fake_models.Document = MagicMock()
    fake_models.Project = MagicMock()

    fake_vector_store = MagicMock()
    fake_vector_store.get_store = MagicMock(return_value=mock_store)

    with patch.dict(
        "sys.modules",
        {
            "app.core.models": fake_models,
            "app.core.rag.vector_store": fake_vector_store,
        },
    ):
        indexed = _bulk_index_chunks(
            "automotive_core_v1",
            [
                (recall_chunk, [0.1] * 256),
                (investigation_chunk, [0.2] * 256),
            ],
            dim=256,
            model_name="fake",
        )

    assert indexed == 2
    fake_vector_store.get_store.assert_called_once_with(dim=256)

    doc_ids = []
    for call in mock_session.execute.call_args_list:
        stmt = call.args[0]
        doc_ids.append(stmt.compile().params["doc_id"])

    assert doc_ids == ["recall:r1", "investigation:i1"]


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


def test_investigation_number_appears_in_every_chunk() -> None:
    from app.models.automotive_records import AutomotiveInvestigation
    records = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    chunks = chunk_investigation_records(records)
    assert all("PE16-007" in c.text for c in chunks)


def test_multi_family_build_writes_both_chunk_files(tmp_path: Path) -> None:
    recalls = _sample_records()
    recall_path = tmp_path / "recalls.jsonl"
    with recall_path.open("w", encoding="utf-8") as f:
        for r in recalls:
            f.write(r.model_dump_json() + "\n")

    from app.models.automotive_records import AutomotiveInvestigation
    investigations = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    inv_path = tmp_path / "investigations.jsonl"
    with inv_path.open("w", encoding="utf-8") as f:
        for r in investigations:
            f.write(r.model_dump_json() + "\n")

    manifest = build_automotive_core_pack_from_families(
        canonical_records_paths=[recall_path, inv_path],
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=True,
    )

    assert "recall" in manifest.source_families
    assert "investigation" in manifest.source_families
    assert (tmp_path / "pack" / "chunks" / "recalls.jsonl").exists()
    assert (tmp_path / "pack" / "chunks" / "investigations.jsonl").exists()
