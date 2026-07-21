"""JSONL vector index persistence for estate dual RAG layers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class IndexStats:
    index_id: str
    layer: int
    record_count: int
    document_count: int


class RagIndexStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def index_dir(self, index_id: str) -> Path:
        return self.base_path / index_id

    def _manifest_path(self, index_id: str) -> Path:
        return self.index_dir(index_id) / "manifest.json"

    def _records_path(self, index_id: str) -> Path:
        return self.index_dir(index_id) / "records.jsonl"

    def read_manifest(self, index_id: str) -> Optional[Dict[str, Any]]:
        path = self._manifest_path(index_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_records(self, index_id: str) -> List[Dict[str, Any]]:
        path = self._records_path(index_id)
        if not path.is_file():
            return []
        records: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records

    def write_index(
        self,
        index_id: str,
        *,
        layer: int,
        manifest_extra: Optional[Dict[str, Any]] = None,
        records: List[Dict[str, Any]],
    ) -> None:
        index_dir = self.index_dir(index_id)
        index_dir.mkdir(parents=True, exist_ok=True)
        doc_ids = sorted({str(r.get("doc_id") or "") for r in records if r.get("doc_id")})
        manifest: Dict[str, Any] = {
            "index_id": index_id,
            "layer": layer,
            "adapter_id": "local_flat_json_v1",
            "embedding_provider": "local_feature_hash_v1",
            "dimensions": 384,
            "distance_metric": "cosine",
            "record_count": len(records),
            "document_count": len(doc_ids),
        }
        if manifest_extra:
            manifest.update(manifest_extra)

        manifest_path = self._manifest_path(index_id)
        records_path = self._records_path(index_id)
        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_records = records_path.with_suffix(".jsonl.tmp")
        try:
            tmp_manifest.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            with tmp_records.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(
                        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
                    )
            os.replace(tmp_manifest, manifest_path)
            os.replace(tmp_records, records_path)
        except Exception:
            tmp_manifest.unlink(missing_ok=True)
            tmp_records.unlink(missing_ok=True)
            raise

    def upsert_document(
        self,
        index_id: str,
        *,
        layer: int,
        doc_id: str,
        new_records: List[Dict[str, Any]],
    ) -> None:
        existing = [
            r for r in self.read_records(index_id) if str(r.get("doc_id") or "") != doc_id
        ]
        existing.extend(new_records)
        self.write_index(index_id, layer=layer, records=existing)

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
