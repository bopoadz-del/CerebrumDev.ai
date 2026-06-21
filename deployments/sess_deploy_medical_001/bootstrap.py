"""Restore the session vector index into ChromaDB on first boot."""
import json
import os
from pathlib import Path

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "/app/chroma")

vectors_path = Path(__file__).parent / "vectors.json"
if not vectors_path.exists():
    print("No vectors.json found; skipping bootstrap.")
    raise SystemExit(0)

with open(vectors_path, encoding="utf-8") as f:
    data = json.load(f)

chunks = data.get("chunks", [])
embeddings = data.get("embeddings", [])
metadatas = data.get("metadatas", []) or [{"index": i} for i in range(len(chunks))]

if not chunks:
    print("No chunks to index.")
    raise SystemExit(0)

os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
try:
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    collection = client.get_or_create_collection(
        name="session_default",
        metadata={"domain": data.get("domain", "general"), "source": "deploy_bootstrap"},
    )
    ids = [f"chunk_{{i}}" for i in range(len(chunks))]
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings if embeddings and len(embeddings) == len(chunks) else None,
        metadatas=metadatas,
    )
    print(f"Bootstrapped {{len(chunks)}} chunks into ChromaDB at {{CHROMA_PERSIST_DIR}}")
except Exception as exc:
    print(f"Bootstrap failed (non-fatal): {{exc}}")
