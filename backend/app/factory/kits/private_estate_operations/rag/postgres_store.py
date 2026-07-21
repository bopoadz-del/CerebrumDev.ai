"""Postgres-backed dual RAG index (JSONB vectors; durable across restarts).

Uses ``STEWARD_DATABASE_URL`` / ``DATABASE_URL``. Cosine ranking stays in-process
(pilot corpus size). Optional ``CREATE EXTENSION vector`` is attempted but not
required — JSONB storage is the durable contract.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.estate_kit.rag.store import IndexStats

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS estate_rag_records (
    record_id TEXT PRIMARY KEY,
    index_id TEXT NOT NULL,
    layer INTEGER NOT NULL,
    doc_id TEXT NOT NULL,
    title TEXT,
    property_id TEXT,
    version TEXT,
    chunk_ordinal INTEGER,
    character_start INTEGER,
    character_end INTEGER,
    text TEXT NOT NULL,
    text_hash TEXT,
    embedding_provider TEXT NOT NULL,
    vector JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_estate_rag_index_id ON estate_rag_records (index_id);
CREATE INDEX IF NOT EXISTS ix_estate_rag_doc ON estate_rag_records (index_id, doc_id);
CREATE INDEX IF NOT EXISTS ix_estate_rag_property ON estate_rag_records (property_id);
"""


class PostgresRagIndexStore:
    adapter_id = "postgres_jsonb_v1"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._ensured = False

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        if self._ensured:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception:  # noqa: BLE001 — optional; JSONB is enough
                    pass
            conn.commit()
        self._ensured = True

    def read_manifest(self, index_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT doc_id),
                           MAX(embedding_provider), MAX(layer)
                    FROM estate_rag_records WHERE index_id = %s
                    """,
                    (index_id,),
                )
                row = cur.fetchone()
        if not row or int(row[0] or 0) == 0:
            return None
        return {
            "index_id": index_id,
            "layer": int(row[3] or 0),
            "adapter_id": self.adapter_id,
            "embedding_provider": row[2],
            "dimensions": 384,
            "distance_metric": "cosine",
            "record_count": int(row[0]),
            "document_count": int(row[1]),
            "durable": True,
        }

    def read_records(self, index_id: str) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT record_id, index_id, layer, doc_id, title, property_id,
                           version, chunk_ordinal, character_start, character_end,
                           text, text_hash, embedding_provider, vector
                    FROM estate_rag_records
                    WHERE index_id = %s
                    ORDER BY doc_id, chunk_ordinal
                    """,
                    (index_id,),
                )
                rows = cur.fetchall()
        records: List[Dict[str, Any]] = []
        for r in rows:
            vector = r[13]
            if isinstance(vector, str):
                vector = json.loads(vector)
            records.append(
                {
                    "record_id": r[0],
                    "index_id": r[1],
                    "layer": int(r[2]),
                    "doc_id": r[3],
                    "title": r[4],
                    "property_id": r[5],
                    "version": r[6],
                    "chunk_ordinal": r[7],
                    "character_start": r[8],
                    "character_end": r[9],
                    "text": r[10],
                    "text_hash": r[11],
                    "embedding_provider": r[12],
                    "vector": list(vector) if vector is not None else [],
                }
            )
        return records

    def write_index(
        self,
        index_id: str,
        *,
        layer: int,
        manifest_extra: Optional[Dict[str, Any]] = None,
        records: List[Dict[str, Any]],
    ) -> None:
        del manifest_extra  # stats derived from rows
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM estate_rag_records WHERE index_id = %s", (index_id,)
                )
                for record in records:
                    cur.execute(
                        """
                        INSERT INTO estate_rag_records (
                            record_id, index_id, layer, doc_id, title, property_id,
                            version, chunk_ordinal, character_start, character_end,
                            text, text_hash, embedding_provider, vector
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                        )
                        """,
                        (
                            record["record_id"],
                            index_id,
                            int(record.get("layer") or layer),
                            record["doc_id"],
                            record.get("title"),
                            record.get("property_id"),
                            record.get("version"),
                            record.get("chunk_ordinal"),
                            record.get("character_start"),
                            record.get("character_end"),
                            record["text"],
                            record.get("text_hash"),
                            record.get("embedding_provider"),
                            json.dumps(record.get("vector") or []),
                        ),
                    )
            conn.commit()

    def upsert_document(
        self,
        index_id: str,
        *,
        layer: int,
        doc_id: str,
        new_records: List[Dict[str, Any]],
    ) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM estate_rag_records WHERE index_id = %s AND doc_id = %s",
                    (index_id, doc_id),
                )
                for record in new_records:
                    cur.execute(
                        """
                        INSERT INTO estate_rag_records (
                            record_id, index_id, layer, doc_id, title, property_id,
                            version, chunk_ordinal, character_start, character_end,
                            text, text_hash, embedding_provider, vector
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                        )
                        """,
                        (
                            record["record_id"],
                            index_id,
                            int(record.get("layer") or layer),
                            record["doc_id"],
                            record.get("title"),
                            record.get("property_id"),
                            record.get("version"),
                            record.get("chunk_ordinal"),
                            record.get("character_start"),
                            record.get("character_end"),
                            record["text"],
                            record.get("text_hash"),
                            record.get("embedding_provider"),
                            json.dumps(record.get("vector") or []),
                        ),
                    )
            conn.commit()

    def stats(self, index_id: str, *, layer: int) -> IndexStats:
        manifest = self.read_manifest(index_id)
        if manifest:
            return IndexStats(
                index_id=index_id,
                layer=layer,
                record_count=int(manifest.get("record_count") or 0),
                document_count=int(manifest.get("document_count") or 0),
            )
        return IndexStats(index_id=index_id, layer=layer, record_count=0, document_count=0)
