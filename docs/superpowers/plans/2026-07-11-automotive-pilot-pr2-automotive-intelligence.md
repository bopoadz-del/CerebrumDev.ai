# Implementation Plan: Automotive Safety Intelligence Pilot — PR 2

> **Plan file:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr2-automotive-intelligence.md`  
> **Parent design spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Branch:** `feat/rag-vector-index-dry-run`  
> **PR title:** `feat(rag): automotive intelligence — NHTSA harvest, embeddings, retrieval, activation and evaluation`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai`  
> **Estimated effort:** large

---

## Outcome

PR 2 produces a working **Automotive Core RAG** layer inside CerebrumDev.ai:

- Official NHTSA public data is harvested, normalized and versioned.
- Canonical automotive record models exist.
- Documents are chunked deterministically using existing chunking machinery.
- Real semantic embeddings are generated with **BAAI/bge-small-en-v1.5** at **384 dimensions** into the **`chunks_v2`** namespace.
- Embeddings are indexed in **Postgres/pgvector** and **BM25**.
- Layer-aware hybrid retrieval returns public foundation evidence, with hooks for later client-overlay blending.
- The foundation RAG pack can be activated.
- A 50-question golden evaluation pack proves the retrieval and citation gates.

No production vector database other than Postgres/pgvector is used. No similarity-search API is exposed to end users yet. No client-private documents are handled in this PR — that belongs to PR 3.

---

## Scope guard

**In scope**

- NHTSA source acquisition and normalization.
- Canonical automotive record models.
- Foundation RAG pack build pipeline (source → acquisition → canonical → chunks → embeddings → index → activate).
- Reuse of existing deterministic chunking, embedding and indexing infrastructure from CerebrumDev.ai.
- Postgres/pgvector and BM25 indexing for the automotive corpus.
- Layer-aware retrieval service (foundation layer only; client-overlay wiring in PR 3).
- Source citation envelope.
- 50-question evaluation pack and evaluation runner.
- Admin verification/activation API for the foundation pack.

**Out of scope**

- Frontend conversion.
- Generated-platform deployment.
- Client-private document upload and project overlay (PR 3).
- Google Drive UI journeys (connector core in PR 1; end-to-end in PR 3).
- Production semantic embedders other than BGE-small-en-v1.5.
- Fine-tuning, LoRA, billing, marketplace, mobile, voice.
- Changes to Cerebrum-Blocks, The_Fork live construction platform, Fork2, chain generation, formula_executor_v2 internals, chat/LLM provider configuration.

---

## Files to inspect before writing

```text
backend/app/core/rag_ingestion_store.py
backend/app/core/rag_canonical_documents.py
backend/app/core/rag_embeddings.py
backend/app/core/rag_embedding_providers.py
backend/app/core/rag_vector_indexing.py
backend/app/core/rag_vector_store_adapters.py
backend/app/models/rag_ingestion.py
backend/app/routers/domains.py
backend/app/config.py
backend/.env.example
backend/tests/test_rag_*.py
backend/scripts/build_foundation_pack.py   (if exists)
```

Also inspect the current Fork-derived platform template produced by PR 1 to confirm:

- Postgres/pgvector schema for chunks and embeddings.
- BM25 table setup.
- Hybrid retrieval service location.
- Citation envelope used by the chat router.

---

## Part A — NHTSA harvester and source manifest

### 1. Source families and authority

Create a versioned source manifest under:

```text
backend/app/data/automotive_core_rag_v1/sources/manifest.json
```

Source families:

```text
recalls           NHTSA Recall Campaigns        https://www.nhtsa.gov/nhtsa-datasets-and-apis
investigations    NHTSA ODI Investigations      https://www-odi.nhtsa.dot.gov/downloads/
complaints        NHTSA Consumer Complaints     https://www-odi.nhtsa.dot.gov/downloads/
safety_ratings    NHTSA 5-Star Safety Ratings   https://www.nhtsa.gov/ratings
vpic              vPIC Make/Model/Manufacturer  https://vpic.nhtsa.dot.gov/api/
```

Each entry must record:

```json
{
  "source_id": "nhtsa_recalls",
  "source_family": "recall",
  "official_publisher": "National Highway Traffic Safety Administration",
  "source_uri": "https://www.nhtsa.gov/nhtsa-datasets-and-apis",
  "retrieval_method": "download_csv_zip",
  "source_class": "approved_official_public_data",
  "licence_evidence": "US federal public data",
  "authority_rating": "primary",
  "jurisdiction": "US",
  "coverage_start": "2015-01-01",
  "coverage_end": "<harvest_date>",
  "harvest_timestamp": "<iso_timestamp>",
  "content_hash": "<sha256_of_raw_archive>",
  "record_count": 0,
  "normalization_version": "automotive_core_v1",
  "chunking_version": "chunks_v2",
  "embedding_identity": "bge-small-en-v1.5-384",
  "pack_version": "automotive_core_rag_v1.0.0",
  "refresh_policy": "monthly_full_rebuild"
}
```

### 2. Harvest CLI

Add:

```text
backend/scripts/harvest_nhtsa.py
```

Behavior:

- Download each family’s dataset to a local working directory outside Git.
- Verify archive hash.
- Extract CSVs.
- Record row counts and harvest timestamp.
- Write raw metadata to:

```text
STORAGE_PATH/automotive_core_rag_v1/harvests/<iso_date>/raw/<source_family>/
```

- Idempotent: skip re-download if archive hash already harvested today.
- No credentials beyond public URLs.

CLI example:

```bash
cd backend
python -m scripts.harvest_nhtsa --output-dir "$STORAGE_PATH/automotive_core_rag_v1/harvests" --dry-run
```

### 3. Raw-data handling rule

The harvested CSVs and extracted rows are **build artifacts**, not repo files. Add to `.gitignore`:

```text
# Automotive foundation pack build artifacts
storage/automotive_core_rag_v1/
data/automotive_core_rag_v1/harvests/
*.nhtsa.zip
```

---

## Part B — Automotive canonical record models

### 1. Model module

Create:

```text
backend/app/models/automotive_records.py
```

Record classes:

```python
class AutomotiveRecall(BaseModel):
    record_type: Literal["recall"] = "recall"
    campaign_number: str
    manufacturer: str | None
    make: str | None
    model: str | None
    model_year: int | str | None
    component: str | None
    summary: str
    consequence: str | None
    remedy: str | None
    report_received_date: date | None
    affected_units: int | None
    source_url: str
    source_family: str = "recall"

class AutomotiveComplaint(BaseModel):
    record_type: Literal["complaint"] = "complaint"
    odi_number: str
    make: str | None
    model: str | None
    model_year: int | str | None
    component: str | None
    incident_date: date | None
    failure_description: str
    crash_indicator: bool | None
    fire_indicator: bool | None
    injury_count: int | None
    death_count: int | None
    source_url: str
    source_family: str = "complaint"

class AutomotiveInvestigation(BaseModel):
    record_type: Literal["investigation"] = "investigation"
    investigation_number: str
    status: str | None
    type: str | None
    make: str | None
    model: str | None
    model_year_range: str | None
    component: str | None
    opening_date: date | None
    closing_date: date | None
    summary: str
    source_url: str
    source_family: str = "investigation"

class AutomotiveSafetyRating(BaseModel):
    record_type: Literal["safety_rating"] = "safety_rating"
    vehicle_id: str
    make: str
    model: str
    model_year: int
    overall_rating: str | None
    frontal_crash_rating: str | None
    side_crash_rating: str | None
    rollover_rating: str | None
    source_url: str
    source_family: str = "safety_rating"

class AutomotiveVehicleIdentity(BaseModel):
    record_type: Literal["vehicle_identity"] = "vehicle_identity"
    make: str
    model: str
    model_year: int | None
    manufacturer: str | None
    component_tags: list[str]
    source_family: str = "vehicle_identity"
```

### 2. Normalizer service

Create:

```text
backend/app/core/automotive_normalizers.py
```

Functions:

```python
def normalize_recall(row: dict[str, Any]) -> AutomotiveRecall: ...
def normalize_complaint(row: dict[str, Any]) -> AutomotiveComplaint: ...
def normalize_investigation(row: dict[str, Any]) -> AutomotiveInvestigation: ...
def normalize_safety_rating(row: dict[str, Any]) -> AutomotiveSafetyRating: ...
def normalize_vehicle_identity(row: dict[str, Any]) -> AutomotiveVehicleIdentity: ...
```

Rules:

- Strip whitespace.
- Coerce model_year to int when possible; leave as string range otherwise.
- Map missing values to `None`, never fabricate.
- Preserve exact NHTSA identifiers.
- Produce deterministic output: same raw row → same normalized record.

### 3. Canonical document bridge

Each normalized record becomes a `RagAcquisition` → `RagCanonicalDocument` via the existing ingestion path.

Add a helper in:

```text
backend/app/core/automotive_pack_builder.py
```

```python
def canonicalize_records(
    records: list[AutomotiveRecall | AutomotiveComplaint | AutomotiveInvestigation | ...],
    pack_id: str,
    collection_id: str,
    domain: str = "automotive",
) -> list[str]:
    """Returns list of canonical document IDs."""
```

Use existing `rag_ingestion_store` and `rag_canonical_documents` to create canonical documents with:

```text
canonicalization_status = canonicalized
chunking_status = not_chunked
index_status = not_indexed
source_class = approved_official_public_data
knowledge_layer = automotive_core_v1
```

---

## Part C — Foundation pack build pipeline

### 1. Builder module

Create:

```text
backend/app/core/automotive_pack_builder.py
backend/scripts/build_automotive_core_pack.py
```

Pipeline stages:

```text
1. load_source_manifest()
2. harvest_or_reuse_raw()
3. normalize_records()
4. canonicalize_records()
5. chunk_documents(deterministic_chunks_v2)
6. generate_embeddings(provider=bge-small-en-v1.5, dimensions=384)
7. create_pgvector_index(namespace=chunks_v2)
8. create_bm25_index(namespace=chunks_v2)
9. verify_index_integrity()
10. update_pack_manifest(status=validated)
11. activate_pack(version=automotive_core_rag_v1.0.0)
```

Each stage is resumable and idempotent. Store progress in:

```text
STORAGE_PATH/automotive_core_rag_v1/build_state.json
```

### 2. Reuse existing embedding provider

The production embedding path is the existing `sentence-transformers` provider already used by The_Fork for `chunks_v2`.

Confirm config keys:

```text
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_EMBEDDING_DIMENSIONS=384
RAG_VECTOR_NAMESPACE=chunks_v2
```

If no production provider exists, fail with `PRODUCTION_EMBEDDING_NOT_CONFIGURED`. Do not fall back to `local_feature_hash_v1` for the pilot.

### 3. Index namespace

Use the same namespace as the existing qualified corpus:

```text
chunks_v2
```

Do not mix embedding identities in this namespace. The builder must verify the existing namespace model/dimensions match before writing.

### 4. Pack manifest

Write:

```text
STORAGE_PATH/automotive_core_rag_v1/pack_manifest.json
```

Content:

```json
{
  "pack_id": "automotive_core_rag_v1",
  "pack_version": "automotive_core_rag_v1.0.0",
  "domain": "automotive",
  "foundation_collection": "automotive_core_v1",
  "status": "active",
  "embedding_identity": "bge-small-en-v1.5-384",
  "vector_namespace": "chunks_v2",
  "record_counts": { "recall": 0, "complaint": 0, "investigation": 0, "safety_rating": 0, "vehicle_identity": 0 },
  "chunk_count": 0,
  "source_manifest_hash": "<sha256>",
  "build_started_at": "<iso>",
  "build_completed_at": "<iso>",
  "harvest_timestamp": "<iso>"
}
```

---

## Part D — Layer-aware hybrid retrieval

### 1. Retrieval service

Create or extend:

```text
backend/app/core/rag_retrieval.py
```

Add:

```python
class RetrievalRequest(BaseModel):
    query: str
    knowledge_layers: list[Literal["automotive_core_v1", "client_private"]] = ["automotive_core_v1"]
    project_id: str | None = None
    top_k_vector: int = 10
    top_k_lexical: int = 10
    fusion_k: int = 60
    exact_identifier_boosts: dict[str, float] | None = None

class RetrievedEvidence(BaseModel):
    knowledge_layer: Literal["automotive_core_v1", "client_private"]
    source_family: str
    source_title: str
    source_authority: str
    source_url: str | None
    record_reference: str
    retrieval_score: float
    chunk_text: str
    metadata: dict[str, Any]

def retrieve_evidence(request: RetrievalRequest) -> list[RetrievedEvidence]: ...
```

### 2. Exact identifier handling

Before vector/lexical retrieval, detect automotive identifiers in the query:

```python
IDENTIFIER_PATTERNS = {
    "campaign_number": r"\b\d{2}[A-Z]\d{3,6}\b",   # example; refine against real NHTSA formats
    "odi_number": r"\b\d{9,10}\b",
    "investigation_number": r"\bDP\d{2,3}\-\d{3,4}\b",
}
```

When matched, issue a direct metadata filter lookup in pgvector/BM25 and boost those records to the top.

### 3. Hybrid fusion

Use the same RRF fusion already present in The_Fork:

```text
score_rrf = 1 / (k + rank_vector) + 1 / (k + rank_lexical)
```

Deduplicate by `(source_family, record_reference)`.

### 4. Layer-aware ranking

For this PR, only the `automotive_core_v1` layer is active. Client-private layer wiring is stubbed but gated by `project_id` presence, to be fully implemented in PR 3.

---

## Part E — Automotive grounded assistant configuration

### 1. System prompt

Create:

```text
backend/app/prompts/automotive_assistant_v1.txt
```

Key instructions:

- Answer only from retrieved evidence.
- Cite recall campaign numbers, ODI numbers, investigation numbers.
- Distinguish complaints from official defect findings.
- Distinguish manufacturer communications from formal recalls.
- State corpus harvest date when freshness matters.
- Say when evidence is insufficient.
- Never diagnose mechanical failures as certain from complaint patterns.

### 2. Chat router integration

Extend the generated platform’s chat router (from PR 1) to:

- Call `retrieve_evidence` before generation.
- Pass evidence as context.
- Stream citations through the SSE envelope already defined in the design spec.
- Render `knowledge_layer` label per citation.

---

## Part F — Evaluation pack

### 1. Golden questions

Create:

```text
backend/app/data/automotive_core_rag_v1/evaluation/golden_questions.jsonl
```

50 items across categories:

```text
10 exact recall/campaign lookups
10 make/model/year recall questions
8 complaint-pattern questions
7 investigation questions
5 safety-rating questions
5 cross-source comparison questions
5 unsupported/no-answer questions
```

Schema per item:

```json
{
  "question_id": "auto-eval-001",
  "category": "exact_recall_lookup",
  "question": "What is the remedy for NHTSA campaign 20V123?",
  "expected_source_family": "recall",
  "expected_identifiers": ["20V123"],
  "required_evidence": ["campaign_number", "remedy"],
  "forbidden_unsupported_claim": true,
  "answerability": "answerable"
}
```

### 2. Evaluation runner

Create:

```text
backend/app/core/automotive_evaluation.py
backend/scripts/run_automotive_evaluation.py
```

Metrics:

```text
exact_campaign_retrieval: pass/fail per item
exact_odi_investigation_retrieval: pass/fail per item
top5_evidence_recall: float
answer_has_required_citation: pass/fail
unsupported_claim_present: pass/fail
```

Gates:

```text
exact campaign-number retrieval: 100%
exact ODI/investigation identifier retrieval: 100%
top-5 evidence recall across answerable questions: >= 85%
answers with required citations: 100%
unsupported evidence presented as fact: 0
```

---

## Part G — Admin foundation-pack API

Add admin-only routes in the generated platform router:

```text
GET  /admin/automotive-core/status
POST /admin/automotive-core/build
POST /admin/automotive-core/verify
POST /admin/automotive-core/evaluate
POST /admin/automotive-core/activate
POST /admin/automotive-core/rollback
```

Behavior:

- `build`: starts or resumes the foundation pack build pipeline.
- `verify`: runs read-back integrity checks and vector-hash validation.
- `evaluate`: runs the 50-question golden pack.
- `activate`: sets `pack_manifest.status = active` after verification passes.
- `rollback`: switches to a prior validated pack version.

Destructive rebuild requires explicit confirmation token in request body.

---

## Part H — Tests

Add:

```text
backend/tests/test_automotive_normalizers.py
backend/tests/test_automotive_pack_builder.py
backend/tests/test_automotive_retrieval.py
backend/tests/test_automotive_evaluation.py
backend/tests/test_admin_automotive_core.py
```

Prove:

- Raw NHTSA rows normalize into canonical records.
- Same raw row produces identical normalized record.
- Missing fields become `None`.
- Builder is idempotent and resumable.
- Embeddings use BGE-small-en-v1.5 at 384 dimensions.
- Vectors are normalized and finite.
- pgvector and BM25 indexes contain expected record counts.
- Exact identifier lookup returns correct record.
- Hybrid retrieval ranks foundation evidence.
- Evaluation gates pass on the built pack.
- Admin endpoints enforce admin role.
- Non-admin cannot activate pack.
- No client-private leakage in foundation results.

---

## Part I — Verification commands

```bash
cd backend

python -m py_compile \
  app/models/automotive_records.py \
  app/core/automotive_normalizers.py \
  app/core/automotive_pack_builder.py \
  app/core/rag_retrieval.py \
  app/core/automotive_evaluation.py \
  app/routers/admin_automotive.py

python -m pytest \
  tests/test_automotive_normalizers.py \
  tests/test_automotive_pack_builder.py \
  tests/test_automotive_retrieval.py \
  tests/test_automotive_evaluation.py \
  tests/test_admin_automotive_core.py \
  -q --tb=short

python -m pytest tests -q
```

---

## Part J — Commit plan

Create commits in this order:

1. `feat(automotive): add NHTSA source manifest and harvester`
2. `feat(automotive): add canonical automotive record models and normalizers`
3. `feat(automotive): add foundation pack builder with BGE-small 384 indexing`
4. `feat(automotive): add layer-aware hybrid retrieval service`
5. `feat(automotive): add automotive assistant prompt and chat integration`
6. `feat(automotive): add 50-question evaluation pack and runner`
7. `feat(automotive): add admin foundation-pack activation API`
8. `test(automotive): add automotive intelligence tests`

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| NHTSA data format changes | Version source manifest; normalize defensively; record harvest timestamp. |
| BGE-small model download failure | Cache model in CI/build environment; fail fast with clear error. |
| Index build is slow | Batch commits; resume state; limit first pilot to 2015+ recalls/investigations and 2022+ complaints. |
| BM25/pgvector schema drift from Fork | Pin to same schema as PR 1 template; integration test before merge. |
| Evaluation gate misses 85% | Iterate retrieval fusion and exact-identifier boosts; report actual score transparently. |

---

## Definition of done

- [ ] NHTSA harvester downloads and records hashes.
- [ ] Canonical record models and normalizers are deterministic.
- [ ] Foundation pack builder produces `automotive_core_rag_v1` with real 384-dim embeddings.
- [ ] Postgres/pgvector and BM25 indexes are queryable.
- [ ] Layer-aware retrieval returns ranked foundation evidence.
- [ ] Admin API can build, verify, evaluate and activate the pack.
- [ ] 50-question evaluation meets gates or failure is documented.
- [ ] All new tests pass and full backend suite passes.
- [ ] CI passes.
