"""Dual-layer RAG ingest + cosine retrieval for generated Steward products."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.estate_kit.rag.chunking import DEFAULT_CHUNKING_VERSION, chunk_hash, chunk_text
from app.estate_kit.rag.embeddings import LOCAL_FEATURE_HASH_V1, cosine_similarity, embed_text
from app.estate_kit.rag.store import IndexStats, RagIndexStore

LAYER_INDEX: Dict[int, str] = {
    1: "steward_sop_v1",
    2: "steward_estate_docs_v1",
}

LAYER_SOURCE_FIELD: Dict[int, str] = {
    1: "sop_corpus",
    2: "estate_documents",
}


@dataclass
class RagHit:
    layer: int
    doc_id: str
    title: str
    excerpt: str
    score: float
    chunk_ordinal: int
    property_id: Optional[str]
    citation: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "doc_id": self.doc_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "score": round(self.score, 6),
            "chunk_ordinal": self.chunk_ordinal,
            "property_id": self.property_id,
            "citation": self.citation,
        }


class DualRagService:
    def __init__(self, product_root: Optional[Path] = None) -> None:
        self.product_root = product_root or Path(__file__).resolve().parents[3]
        self.store = RagIndexStore(self.product_root / "data" / "rag")
        self._bootstrap_lock = threading.Lock()
        self._bootstrapped = False

    def ensure_bootstrapped(self) -> None:
        if self._bootstrapped:
            return
        with self._bootstrap_lock:
            if self._bootstrapped:
                return
            if self._indices_empty():
                self.bootstrap_from_fixtures()
            self._bootstrapped = True

    def _indices_empty(self) -> bool:
        for index_id in LAYER_INDEX.values():
            if self.store.read_records(index_id):
                return False
        return True

    def bootstrap_from_fixtures(self) -> Dict[str, Any]:
        fixtures_path = self.product_root / "data" / "demo" / "estate_fixtures.json"
        if not fixtures_path.is_file():
            return {"ok": False, "reason": "fixtures_missing"}
        data = json.loads(fixtures_path.read_text(encoding="utf-8"))
        ingested: List[str] = []
        for layer, field in LAYER_SOURCE_FIELD.items():
            for doc in data.get(field) or []:
                doc_id = str(doc.get("doc_id") or "")
                title = str(doc.get("title") or "")
                text = str(doc.get("text") or "")
                if not doc_id or not text:
                    continue
                self.ingest_document(
                    layer=layer,
                    doc_id=doc_id,
                    title=title,
                    text=text,
                    property_id=doc.get("property_id"),
                    version=doc.get("version"),
                )
                ingested.append(doc_id)
        return {"ok": True, "ingested_doc_ids": ingested}

    def ingest_document(
        self,
        *,
        layer: int,
        doc_id: str,
        title: str,
        text: str,
        property_id: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        if layer not in LAYER_INDEX:
            raise ValueError(f"Unsupported layer: {layer}")
        if not doc_id.strip():
            raise ValueError("doc_id is required")
        if not text.strip():
            raise ValueError("text is required")

        index_id = LAYER_INDEX[layer]
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks = chunk_text(text)
        vectors = [embed_text(chunk.text) for chunk in chunks]
        records: List[Dict[str, Any]] = []
        for chunk, vector in zip(chunks, vectors):
            chunk_id = chunk_hash(
                doc_id,
                DEFAULT_CHUNKING_VERSION,
                chunk.ordinal,
                chunk.character_start,
                chunk.character_end,
                text_hash,
            )
            records.append(
                {
                    "record_id": chunk_id,
                    "index_id": index_id,
                    "layer": layer,
                    "doc_id": doc_id,
                    "title": title,
                    "property_id": property_id,
                    "version": version,
                    "chunk_ordinal": chunk.ordinal,
                    "character_start": chunk.character_start,
                    "character_end": chunk.character_end,
                    "text": chunk.text,
                    "text_hash": text_hash,
                    "embedding_provider": LOCAL_FEATURE_HASH_V1,
                    "vector": vector,
                }
            )
        self.store.upsert_document(index_id, layer=layer, doc_id=doc_id, new_records=records)
        return {
            "ok": True,
            "layer": layer,
            "index_id": index_id,
            "doc_id": doc_id,
            "chunk_count": len(records),
        }

    def query(
        self,
        q: str,
        *,
        layer: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.05,
        property_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_bootstrapped()
        if not q.strip():
            raise ValueError("query is required")

        layers: List[int] = []
        if layer is None:
            layers = sorted(LAYER_INDEX)
        elif layer in LAYER_INDEX:
            layers = [layer]
        else:
            raise ValueError(f"Unsupported layer: {layer}")

        query_vector = embed_text(q)
        hits: List[RagHit] = []
        for lyr in layers:
            index_id = LAYER_INDEX[lyr]
            for record in self.store.read_records(index_id):
                rec_property = record.get("property_id")
                if property_id:
                    # Layer 1 globals (null property) stay visible; Layer 2 must match.
                    if lyr == 2 and str(rec_property or "") != str(property_id):
                        continue
                    if lyr == 1 and rec_property and str(rec_property) != str(property_id):
                        continue
                vector = record.get("vector") or []
                if not vector:
                    continue
                score = cosine_similarity(query_vector, vector)
                if score < min_score:
                    continue
                doc_id = str(record.get("doc_id") or "")
                title = str(record.get("title") or "")
                text = str(record.get("text") or "")
                property_val = record.get("property_id")
                hits.append(
                    RagHit(
                        layer=lyr,
                        doc_id=doc_id,
                        title=title,
                        excerpt=text[:240],
                        score=score,
                        chunk_ordinal=int(record.get("chunk_ordinal") or 0),
                        property_id=str(property_val) if property_val else None,
                        citation={
                            "source_id": doc_id,
                            "layer": lyr,
                            "property_id": property_val,
                            "chunk_ordinal": int(record.get("chunk_ordinal") or 0),
                            "record_id": record.get("record_id"),
                        },
                    )
                )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        top_hits = hits[: max(1, top_k)] if hits else []
        return {
            "ok": True,
            "query": q,
            "layers_searched": layers,
            "property_id_filter": property_id,
            "min_score": min_score,
            "hit_count": len(top_hits),
            "hits": [hit.to_dict() for hit in top_hits],
            "insufficiency": len(top_hits) == 0,
            "embedding_provider": LOCAL_FEATURE_HASH_V1,
        }

    def index_stats(self) -> List[IndexStats]:
        self.ensure_bootstrapped()
        return [
            self.store.stats(index_id, layer=layer)
            for layer, index_id in sorted(LAYER_INDEX.items())
        ]


_SERVICE: Optional[DualRagService] = None


def get_rag_service() -> DualRagService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DualRagService()
    return _SERVICE
