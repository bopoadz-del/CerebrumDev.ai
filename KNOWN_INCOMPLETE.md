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
- backend/app/factory/build/gates.py :: __call__  — Gate Protocol declaration (structural typing for a phase check); the real bodies are the module-level gate functions gate_gaps_enumerated / gate_blocks_import_offline / gate_workspace_compiles / gate_suite_green / gate_store_ops_authorised, wired in the GATES mapping.

## Benign guarded fallback
- backend/app/resident_engineer/router.py :: _resolve_principal  — the `else` (non-estate, no steward-auth module) branch returns None; every state-changing resident route fails closed (401) when the principal is None, so a None here authorizes nothing.

## Deliberate no-op by policy
- backend/alembic/versions/0001_baseline.py :: downgrade  — the baseline migration's reverse would be `DROP TABLE` on every accounts table; destroying user/account data is never an acceptable automated rollback. Schema rollback below the baseline is a manual, operator-decided act (restore from backup).

---

## Open work — registered, not fixed

Not hollow functions. These are known-incomplete *systems*, listed so nobody
reads a green suite as "finished". `scripts/audit_stubs.py` ignores this
section (it only parses `- ` lines containing `::`).

### 1. Factory role runner — NOT BUILT
The manufacturing kernel is three of four pieces: `app/factory/build/`
provides `authority.py` (role write-lanes), `ledger.py` (durable resumable
run record) and `gates.py` (per-phase verification). **Nothing binds them
together and nothing calls them.** There is no runner that walks
COLLECTOR → CLONER → WRITER → TESTER → STORE_MANAGER, hands each role a
lane-restricted writer, records verdicts, or drives the writer/tester rework
loop. The five roles themselves do not exist either.

Consequence: the kernels are enforced nowhere at runtime. Product generation
still goes through `ProductGenerator` unchanged, which renders templates and
emits `httpx.post(store_url + "/v1/execute")` handlers for REUSE
capabilities. The factory rebuild is **in progress, not concluded**.

### 2. CEREBRUM_LLM_API_KEY missing on the Render backend — BLOCKS PRODUCTION
The Render service `cerebrumdev-backend` (`srv-d9ta2pad0e5s738lllpg`) has no
LLM key set. Factory drafting there falls back to keyword mode and the coder
cannot run at all, so a production factory run cannot produce coder-written
capabilities. Needs a dashboard secret set by the operator; not fixable in
code. Note `/ready`'s `llm_configured` field reports `true` regardless
(`app/main.py`, `or os.getenv("LLM_PROVIDER")`) — do not trust it as evidence
the LLM works.

### 3. Steward blocks dual-register against the factory's own mirror
`estate_registry` and `portfolio_rollup` do not exist in the real
Cerebrum-Blocks repo (109 blocks, neither present).
`dual_registry.load_blocks_registry` merges `app/factory/vendor_blocks_mirror`
into the registry unconditionally, so those two satisfy the dual-registration
gate against a copy the factory ships to itself. Pointing `blocks_root` at a
real Store checkout changes nothing. Pinned by
`test_steward_blocks_come_from_the_mirror_not_the_store`, which is designed to
go red when they land upstream — that is the cue to drop them from the mirror.

### 4. Generation is not byte-reproducible when the coder runs
Same blueprint, two builds, different tree hash — because an LLM does not emit
the same bytes twice. Not a defect in itself, but it is a real constraint on
the role runner's resume path, which compares an inputs hash to decide whether
a partially-complete build may continue. Blueprint-input hashing is safe;
any provenance check that hashes the *generated tree* is permanently unstable
and must not be added. The suite now asserts the narrower true guarantee (see
"Running the tests" in README.md).
