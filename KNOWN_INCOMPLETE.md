# KNOWN_INCOMPLETE — CerebrumDev.ai

Honest register of functions `scripts/audit_stubs.py` flags as hollow in the
shipping `backend/app/` tree. Every entry below is either a `Protocol`
interface declaration (structural typing — a `...` body is correct; the real
implementation lives in the same module) or a benign, guarded fallback. There
are **no** unimplemented functions on a user/demo path.

Format: `- <path> :: <name>  — <reason>`

## Protocol interface declarations (`...` is correct; impl is real)
The estate dual-RAG layer IS implemented — `RagIndexStore` (JSONL persistence)
and `HashEmbedder` / FastEmbed provide the real bodies. These entries are the
`Protocol` method signatures used for structural typing.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: read_manifest  — RagIndexStoreProtocol declaration; impl at RagIndexStore.read_manifest.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: read_records  — RagIndexStoreProtocol declaration; impl at RagIndexStore.read_records.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: write_index  — RagIndexStoreProtocol declaration; impl at RagIndexStore.write_index.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: upsert_document  — RagIndexStoreProtocol declaration; impl at RagIndexStore.upsert_document.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: stats  — RagIndexStoreProtocol declaration; impl at RagIndexStore.stats.
- backend/app/factory/kits/private_estate_operations/rag/embeddings.py :: embed  — Embedder Protocol declaration; impl at HashEmbedder.embed (+ FastEmbed provider).

## Benign guarded fallback
- backend/app/resident_engineer/router.py :: _resolve_principal  — the `else` (non-estate, no steward-auth module) branch returns None; every state-changing resident route fails closed (401) when the principal is None, so a None here authorizes nothing.
