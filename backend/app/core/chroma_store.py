"""Persistent vector storage for session chunks using ChromaDB.

This module is deliberately small and free of FastAPI/session-store cycles so it
can be imported safely by both `upload_processor` and `session_store`.
"""

import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", os.path.join(STORAGE_PATH, "chroma"))

_chroma_client = None


def _get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        Path(CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    return _chroma_client


def collection_name(session_id: str) -> str:
    """Return a Chroma-safe collection name for a session."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return f"session_{safe}"[:63]


def collection_exists(session_id: str) -> bool:
    """Check whether a collection already exists on disk."""
    try:
        client = _get_chroma_client()
        name = collection_name(session_id)
        # list_collections() returns collection names.
        return name in (c.name if hasattr(c, "name") else str(c) for c in client.list_collections())
    except Exception as exc:
        logger.warning("Failed to check Chroma collection existence: %s", exc)
        return False


def store_chunks(
    session_id: str,
    chunks: List[str],
    embeddings: Optional[List[List[float]]] = None,
    source_files: Optional[List[str]] = None,
) -> bool:
    """Persist chunks + embeddings + metadata to a per-session ChromaDB collection.

    Args:
        session_id: Session identifier.
        chunks: List of text chunks.
        embeddings: Optional parallel list of dense vectors.
        source_files: Optional parallel list of source file names.

    Returns:
        True on success, False on failure.
    """
    if not chunks:
        return True

    try:
        client = _get_chroma_client()
        name = collection_name(session_id)
        now = datetime.now(timezone.utc).isoformat()
        collection = client.get_or_create_collection(
            name=name,
            metadata={
                "session_id": session_id,
                "hnsw:space": "cosine",
                "created_at": now,
                "updated_at": now,
                "source_files": ",".join(source_files or []),
            },
        )

        ids = [f"chunk_{i}" for i in range(len(chunks))]
        metadatas: List[Dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            meta = {
                "session_id": session_id,
                "index": i,
                "created_at": now,
            }
            if source_files and i < len(source_files):
                meta["source_file"] = source_files[i]
            metadatas.append(meta)

        collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings if embeddings and len(embeddings) == len(chunks) else None,
            metadatas=metadatas,
        )
        logger.info("Stored %d chunks in Chroma collection %s", len(chunks), name)
        return True
    except Exception as exc:
        logger.exception("Failed to store chunks in ChromaDB: %s", exc)
        return False


def load_chunks(session_id: str) -> Tuple[List[str], List[List[float]], List[Dict[str, Any]]]:
    """Load chunks, embeddings and metadata for a session from ChromaDB.

    Returns:
        (chunks, embeddings, metadatas) ordered by the stored `index` metadata.
    """
    client = _get_chroma_client()
    name = collection_name(session_id)
    collection = client.get_collection(name=name)
    result = collection.get(include=["documents", "embeddings", "metadatas"])

    documents = result.get("documents") or []
    raw_embeddings = result.get("embeddings")
    metadatas = result.get("metadatas") or []

    # Chroma may return numpy arrays; normalize to plain Python lists.
    if raw_embeddings is None:
        embeddings = []
    elif hasattr(raw_embeddings, "tolist"):
        embeddings = raw_embeddings.tolist()
    else:
        embeddings = list(raw_embeddings)

    # Restore original chunk order using the `index` metadata field.
    combined = list(zip(documents, embeddings, metadatas))
    combined.sort(key=lambda item: (item[2] or {}).get("index", 0))
    if not combined:
        return [], [], []
    docs, embs, metas = zip(*combined)
    return list(docs), list(embs), list(metas)


def count_chunks(session_id: str) -> int:
    """Return the number of chunks stored for a session."""
    try:
        client = _get_chroma_client()
        name = collection_name(session_id)
        collection = client.get_collection(name=name)
        return collection.count()
    except Exception as exc:
        logger.warning("Failed to count chunks for %s: %s", session_id, exc)
        return 0


def load_session_upload(session_id: str) -> Optional[Dict[str, Any]]:
    """Rehydrate a session's chunks/embeddings from its ChromaDB collection.

    Returns None if the collection does not exist or cannot be read.
    """
    if not collection_exists(session_id):
        return None
    try:
        chunks, embeddings, metadatas = load_chunks(session_id)
        source_files = sorted({m.get("source_file") for m in metadatas if m.get("source_file")})
        return {
            "chunks": chunks,
            "embeddings": embeddings,
            "indexed_collection": collection_name(session_id),
            "total_chunks": len(chunks),
            "source_files": source_files,
        }
    except Exception as exc:
        logger.exception("Failed to reload session %s from ChromaDB: %s", session_id, exc)
        return None
