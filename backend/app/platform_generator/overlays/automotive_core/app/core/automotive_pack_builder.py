"""Foundation pack builder for the Automotive Core RAG.

Reads canonical recall records produced by the factory harvester, compiles
deterministic retrieval chunks, embeds them with the configured semantic model,
and indexes them into the generated platform's vector store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.automotive_normalizers import normalize_recall_rows
from app.models.automotive_records import AutomotiveChunk, AutomotiveRecall, PackManifest

logger = logging.getLogger(__name__)

CHUNKING_VERSION = "v2"
FOUNDATION_PACK_ID = "automotive_core_rag_v1"
FOUNDATION_COLLECTION = "automotive_core_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunk_id(record_id: str, index: int) -> str:
    data = f"{record_id}:{CHUNKING_VERSION}:{index}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compile_record_text(record: AutomotiveRecall) -> str:
    """Build a single retrieval text for a recall record.

    The field order is stable so re-running the builder produces byte-identical
    chunks when the source data is unchanged.
    """
    parts: List[str] = []
    if record.campaign_number:
        parts.append(f"Campaign: {record.campaign_number}")
    if record.model_year:
        parts.append(f"Year: {record.model_year}")
    if record.make:
        parts.append(f"Make: {record.make}")
    if record.model:
        parts.append(f"Model: {record.model}")
    if record.manufacturer:
        parts.append(f"Manufacturer: {record.manufacturer}")
    if record.component:
        parts.append(f"Component: {record.component}")
    if record.summary:
        parts.append(f"Summary: {record.summary}")
    if record.consequence:
        parts.append(f"Consequence: {record.consequence}")
    if record.remedy:
        parts.append(f"Remedy: {record.remedy}")
    if record.report_received_date:
        parts.append(f"Report date: {record.report_received_date}")
    return "\n".join(parts)


def chunk_recall_records(
    records: List[AutomotiveRecall],
) -> List[AutomotiveChunk]:
    """Convert canonical recall records into deterministic retrieval chunks.

    One record becomes one chunk in this slice. The chunk text always includes
    the campaign number and, when present, make/model/year and component.
    """
    chunks: List[AutomotiveChunk] = []
    for record in records:
        text = _compile_record_text(record)
        text = text.strip()
        if not text:
            logger.warning("Skipping evidence-free record %s", record.record_id)
            continue
        chunk_index = 0
        chunk_id = _chunk_id(record.record_id, chunk_index)
        chunks.append(
            AutomotiveChunk(
                chunk_id=chunk_id,
                record_id=record.record_id,
                source_id=record.source_id,
                source_family=record.source_family,
                campaign_number=record.campaign_number,
                make=record.make,
                model=record.model,
                model_year=record.model_year,
                component=record.component,
                knowledge_layer=FOUNDATION_COLLECTION,
                foundation_pack_id=FOUNDATION_PACK_ID,
                source_authority=record.authority_rating,
                jurisdiction=record.jurisdiction,
                chunk_index=chunk_index,
                chunking_version=CHUNKING_VERSION,
                text=text,
                text_hash=_text_hash(text),
                record_reference=record.campaign_number,
                source_url=record.source_url,
                metadata={
                    "report_received_date": record.report_received_date,
                    "affected_units": record.affected_units,
                    "raw_record_hash": record.raw_record_hash,
                    "normalization_version": record.normalization_version,
                },
            )
        )
    return chunks


def _require_production_embedder() -> None:
    """Fail loud if the production semantic embedder is unavailable."""
    from app.core.rag.embeddings import Embedder

    if not Embedder.available():
        raise RuntimeError(
            "PRODUCTION_EMBEDDING_NOT_CONFIGURED: "
            "Install sentence-transformers or model2vec, or set RAG_EMBEDDING_MODEL=fake for tests."
        )


def _verify_embedding_identity(identity: Dict[str, Any]) -> None:
    """Validate that the active embedder matches the pack's declared identity."""
    expected_model = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    expected_dim = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "384"))
    if identity.get("model") != expected_model or identity.get("dim") != expected_dim:
        raise RuntimeError(
            f"Embedding identity mismatch: expected {expected_model}@{expected_dim}, "
            f"found {identity.get('model')}@{identity.get('dim')}"
        )


def _embedding_batch_size() -> int:
    """Read ``RAG_EMBEDDING_BATCH_SIZE`` live; default tuned for CPU safety."""
    raw = os.getenv("RAG_EMBEDDING_BATCH_SIZE", "64")
    try:
        value = int(raw)
        return max(1, min(value, 512))
    except ValueError:
        return 64


def _embed_chunks(
    chunks: List[AutomotiveChunk],
    batch_size: Optional[int] = None,
):
    """Yield ``(chunk, embedding)`` pairs in batches.

    Batching keeps peak memory bounded and lets a large corpus stream through
    the embedder without holding every vector in RAM at once.
    """
    from app.core.rag.embeddings import get_embedder

    embedder = get_embedder()
    _verify_embedding_identity(embedder.identity)
    batch_size = batch_size or _embedding_batch_size()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text for c in batch]
        embeddings = embedder.encode(texts)
        if embeddings.shape != (len(batch), embedder.dim):
            raise RuntimeError(
                f"Embedding shape mismatch: expected ({len(batch)}, {embedder.dim}), got {embeddings.shape}"
            )
        if not np.all(np.isfinite(embeddings)):
            raise RuntimeError("Embedding produced non-finite values.")
        for chunk, embedding in zip(batch, embeddings):
            yield chunk, embedding


def _bulk_index_batch_size() -> int:
    """Read ``RAG_BULK_INDEX_BATCH_SIZE`` live; default keeps PostgreSQL
    statements well under Render remote connection limits.
    """
    raw = os.getenv("RAG_BULK_INDEX_BATCH_SIZE", "25")
    try:
        value = int(raw)
        return max(1, min(value, 1000))
    except ValueError:
        return 25


def _bulk_index_chunks(
    project_id: str,
    chunk_embedding_pairs,
    dim: int,
    model_name: str,
) -> int:
    """Bulk-write chunk/embedding rows in bounded batches.

    Replaces the per-document ``VectorStore.upsert_chunks`` path because
    foundation-pack ingestion can be thousands of recall records; one
    transaction per document over a remote PostgreSQL connection is
    unusably slow (observed ~10 s/upsert on Render Oregon). Batched bulk
    inserts commit in seconds while keeping each statement small enough
    for remote Postgres connection limits.

    Upsert semantics: rows are inserted, and on primary-key conflict the
    existing row is updated. This makes re-running the pack idempotent
    without wiping unrelated data.

    Note: the full AutomotiveChunk provenance (source_url, report date,
    affected units, raw record hash) lives in the published chunks JSONL.
    The dynamic ``chunks_v2`` table carries a ``metadata`` JSONB column,
    but the generated ORM class does not populate it yet; this is tracked
    as follow-up work and does not affect retrieval or citation identity.
    """
    from datetime import datetime, timezone

    from sqlalchemy import insert, select
    from sqlalchemy.dialects import postgresql, sqlite

    from app.core.models import Document, Project
    from app.core.rag.vector_store import get_store

    store = get_store(dim=dim)
    table = store._rag_chunk_cls.__table__
    now = datetime.now(timezone.utc).isoformat()
    bulk_batch_size = _bulk_index_batch_size()

    rows = []
    doc_ids: set[str] = set()
    for chunk, embedding in chunk_embedding_pairs:
        # One NHTSA campaign can cover multiple year/make/model rows, so the
        # doc_id must be unique per canonical record (record_id) rather than
        # per campaign number. chunk_index stays 0 because each record becomes
        # exactly one chunk in this slice.
        doc_id = f"recall:{chunk.record_id}"
        doc_ids.add(doc_id)
        text = chunk.text
        if "\x00" in text:
            text = text.replace("\x00", "")
        rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "project_id": project_id,
                "doc_id": doc_id,
                "chunk_index": chunk.chunk_index,
                "text": text,
                "embedding": embedding,
                "created_at": now,
                "embedding_model": model_name,
                "embedding_dim": dim,
                "embedding_normalized": True,
            }
        )

    if not rows:
        return 0

    indexed = 0
    with store._lock:
        with store._session_factory()() as session:
            dialect = session.bind.dialect.name

            # PostgreSQL enforces the chunks FK constraints on projects/documents.
            # SQLite test/dev databases use bare ids without FK enforcement, so
            # skip parent row creation there to avoid requiring the full schema.
            if dialect == "postgresql":
                # Ensure the foundation project exists.
                if session.get(Project, project_id) is None:
                    session.add(
                        Project(
                            id=project_id,
                            name=project_id,
                            client=None,
                            status="active",
                            aconex_connected=False,
                            user_id="system",
                            created_at=now,
                        )
                    )
                    session.flush()

                # Ensure every referenced document exists in bounded batches.
                missing_doc_ids = list(doc_ids)
                for i in range(0, len(missing_doc_ids), bulk_batch_size):
                    batch_ids = missing_doc_ids[i : i + bulk_batch_size]
                    existing_docs = {
                        row[0]
                        for row in session.execute(
                            select(Document.id).where(Document.id.in_(batch_ids))
                        ).all()
                    }
                    new_docs = set(batch_ids) - existing_docs
                    if new_docs:
                        doc_rows = [
                            {
                                "id": doc_id,
                                "project_id": project_id,
                                "original_name": doc_id,
                                "stored_as": None,
                                "file_path": None,
                                "doc_type": "document",
                                "doc_role": "other",
                                "size": 0,
                                "uploaded_at": now,
                                "content_sha256": None,
                            }
                            for doc_id in new_docs
                        ]
                        stmt = postgresql.insert(Document.__table__).values(doc_rows)
                        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                        session.execute(stmt)
                        session.flush()

            # Bulk upsert chunks in bounded batches.
            for i in range(0, len(rows), bulk_batch_size):
                batch = rows[i : i + bulk_batch_size]
                if dialect == "postgresql":
                    stmt = postgresql.insert(table).values(batch)
                    update_dict = {
                        c.name: stmt.excluded[c.name]
                        for c in stmt.excluded
                        if c.name != "chunk_id"
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["chunk_id"], set_=update_dict
                    )
                elif dialect == "sqlite":
                    stmt = sqlite.insert(table).values(batch)
                    update_dict = {
                        c.name: stmt.excluded[c.name]
                        for c in stmt.excluded
                        if c.name != "chunk_id"
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["chunk_id"], set_=update_dict
                    )
                else:
                    for row in batch:
                        session.execute(insert(table).values(**row))
                        indexed += 1
                    session.commit()
                    continue

                result = session.execute(stmt)
                session.commit()
                # SQLAlchemy/PostgreSQL sometimes reports -1 for upsert rowcount;
                # fall back to the batch size so the manifest reflects work done.
                rc = result.rowcount
                indexed += int(rc if rc is not None and rc >= 0 else len(batch))

    return indexed


def _index_chunks(
    project_id: str,
    chunk_embedding_pairs,
    dim: int,
    model_name: str,
) -> int:
    """Write chunks and embeddings to the vector store using bulk upsert."""
    return _bulk_index_chunks(project_id, chunk_embedding_pairs, dim, model_name)


def build_automotive_core_pack(
    canonical_records_path: Path,
    output_dir: Path,
    project_id: str = "automotive_core_v1",
    dry_run: bool = False,
) -> PackManifest:
    """Build the automotive foundation pack from canonical recall records.

    Args:
        canonical_records_path: Path to recalls.jsonl produced by the harvester.
        output_dir: Directory for pack artifacts (manifest, chunks JSONL).
        project_id: Vector-store project id for the foundation corpus.
        dry_run: If True, compile and chunk but do not embed or index.

    Returns:
        PackManifest describing the built pack.
    """
    from app.core.rag.embeddings import get_embedder

    output_dir = Path(output_dir)
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    records = load_canonical_records(canonical_records_path)

    chunks = chunk_recall_records(records)

    chunks_path = chunks_dir / "recalls.jsonl"
    tmp_chunks = chunks_path.with_suffix(".jsonl.tmp")
    with tmp_chunks.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")
    os.replace(tmp_chunks, chunks_path)

    embedding_identity: Dict[str, Any] = {"model": "fake", "dim": 384, "normalized": True}
    indexed_count = 0

    if not dry_run:
        _require_production_embedder()
        embedder = get_embedder()
        embedding_identity = embedder.identity
        indexed_count = _index_chunks(
            project_id,
            _embed_chunks(chunks),
            dim=embedder.dim,
            model_name=embedder.identity.get("model", os.getenv("RAG_EMBEDDING_MODEL", "fake")),
        )

    manifest = PackManifest(
        pack_id=FOUNDATION_PACK_ID,
        pack_version="automotive_core_rag_v1.0.0",
        foundation_collection=FOUNDATION_COLLECTION,
        harvest_timestamp=records[0].harvest_timestamp if records else _utc_now(),
        build_timestamp=_utc_now(),
        record_count=len(records),
        chunk_count=len(chunks),
        embedding_identity=embedding_identity,
        source_families=["recall"],
        status="indexed" if indexed_count > 0 else "validated",
    )

    manifest_path = output_dir / "pack_manifest.json"
    tmp_manifest = manifest_path.with_suffix(".json.tmp")
    tmp_manifest.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_manifest, manifest_path)

    return manifest


def load_canonical_records(path: Path) -> List[AutomotiveRecall]:
    """Load a JSONL canonical records file."""
    records: List[AutomotiveRecall] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(AutomotiveRecall.model_validate(json.loads(line)))
    return records
