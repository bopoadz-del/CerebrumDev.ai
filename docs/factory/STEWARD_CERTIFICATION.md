# Steward Factory Certification Report

**Product:** Cerebrum Steward — Remote Asset & Estate Operations Platform  
**Factory repo:** `bopoadz-del/CerebrumDev.ai`  
**Product repo:** `bopoadz-del/Cerebrum-Steward` (private; FACTORY-GENERATED)  
**Live URL:** https://cerebrum-steward.onrender.com  
**Run log:** `docs/factory/certification-runs/steward-20260721T080317Z.log.jsonl`  
**Factory tip at dual-RAG generate:** `df284a3b976f60ed4397dc5d41d848d22e50471e` (merge of `#74`)  
**Sibling tip pushed:** `cf299d928feac0a594dcda4ac1b5e7f89d3d1903` (`main`, 2026-07-21T10:57:01Z)  
**Render service:** `srv-d9fl136rnols73c72nf0` (`cerebrum-steward`)  
**Render deploy:** `dep-d9fl149hefhs73bbb6i0` — **live** (started `2026-07-21T10:58:05Z`, finished `2026-07-21T10:58:51Z`)

---

## Verdict

**FACTORY CERTIFIED — generation, sibling publish, and live Render verify**

The Factory built Steward end-to-end from the estate kit blueprint (Product DNA,
Resident runtime, demo fixtures, dual RAG surfaces, deploy packaging). Output was
pushed to the pre-existing private sibling `bopoadz-del/Cerebrum-Steward` (no
`gh repo create`; first push defined `main`). Render web service is live and
remote endpoints verified (free-tier cold-start retries OK).

**Still not production RAG:** dual RAG on the live service uses local
feature-hash embeddings + JSONL indices under ephemeral `data/rag/`. Production
Postgres/pgvector + FastEmbed packs remain issue `#73`.

No hand-patch of product code was performed; all product files came from Factory
regeneration.

---

## Timeline (UTC)

| Stage | Timestamp | Notes |
|-------|-----------|-------|
| Certification run start | `2026-07-21T08:03:17Z` | Phase 1 local generate |
| Generate end (first) | `2026-07-21T08:03:18Z` | ~1s wall clock |
| GitHub repo create attempt | `2026-07-21T08:03:31Z` | **403** — agent cannot create repos (expected) |
| Factory upgrade PRs | `#69`–`#71` | Kit, vendor mirrors, Resident `/status` |
| Certification report (deploy pending) | `#72` @ ~`08:07Z` | Local-only verdict |
| Dual RAG Factory upgrade | `#74` merged `2026-07-21T10:53:52+03:00` → `df284a3` | Ingest + vector query |
| Sibling push to `main` | `2026-07-21T10:57:01Z` | `cf299d9` — push only, repo already existed |
| Render deploy live | `2026-07-21T10:58:51Z` | `dep-d9fl149hefhs73bbb6i0` |
| Live remote verify | `2026-07-21T11:00:57Z`–`11:02:00Z` | Health, estate demo, dual RAG, query, ingest |

**Elapsed (kit → live URL):** Phase 0–2 spanned ~`07:55`–`11:02` UTC including CI and deploy; pure generate remains sub-second once the Factory tip is green.

### Phase 0–2 Factory PRs

| PR | Result |
|----|--------|
| [#69](https://github.com/bopoadz-del/CerebrumDev.ai/pull/69) estate kit blueprint + shelf | Merged; Backend was red in CI (no Blocks checkout) — **process miss: merged before green** |
| [#70](https://github.com/bopoadz-del/CerebrumDev.ai/pull/70) vendor-mirror platform blocks + `storage/` gitignore fix | Merged **when green** |
| [#71](https://github.com/bopoadz-del/CerebrumDev.ai/pull/71) Resident `/status` inject + runtime packaging | Merged when green |
| [#72](https://github.com/bopoadz-del/CerebrumDev.ai/pull/72) certification report (deploy pending) | Merged |
| [#74](https://github.com/bopoadz-del/CerebrumDev.ai/pull/74) dual RAG ingest + query | Merged when green |

---

## Generated vs hand-written

| Category | Estimate | Justification |
|----------|----------|----------------|
| **Factory-generated product tree** | **~98%** of product files | `ProductGenerator` emit: actions, hats, workflows, UI stubs, connectors, DNA, Resident, estate kit router, dual RAG, fixtures, Dockerfile/Procfile |
| **Factory-side kit work** | Manual in Factory repo only | Blueprint YAML, shelf entries, vendor mirrors, demo fixtures, generator templates — **allowed** by doctrine |
| **Hand-patches inside product repo** | **0%** | No product files edited after generate; sibling receives regenerated trees only |
| **Human / owner actions** | Repo pre-created empty | Owner created private `Cerebrum-Steward` (no auto-init README); agent pushed + connected Render |

**Honest percentage for “product code written by Factory packager”:** **100% of the generated tree** (~225 files excluding `.git`).  
**Honest percentage for “end-to-end Factory mission including live deploy”:** **~92%** — generation, sibling publish, Render live verify done; production embeddings/packs (`#73`) and deeper Blocks estate logic remain.

---

## Live oracle suite (issue #73) — PASS 10/10

**URL:** https://cerebrum-steward.onrender.com  
**Ran (UTC):** 2026-07-21T11:20:47Z  
**Embedding used on live service:** local_feature_hash_v1 (feature-hash demo path — **not** ge-small / FastEmbed)  
**Suite:** ackend/app/factory/kits/private_estate_operations/evaluation/steward_live_oracle_v1.json  
**Results artifact:** docs/factory/certification-runs/steward-live-oracle-20260721.md

| # | Case | Query | Expected layer | Actual layer | Cited source | Pass/Fail |
|---|------|-------|----------------|--------------|--------------|-----------|
| 1 | principal_guest_arrival | Prepare the arrival checklist for the principal guests | 1 | 1 | sop-global-guest-arrival | **PASS** |
| 2 | turndown_service | How should turndown service be performed? | 1 | 1 | sop-global-turndown | **PASS** |
| 3 | privacy_discretion | privacy discretion restricted information handling | 1 | 1 | sop-global-privacy | **PASS** |
| 4 | housekeeping_defect_escalation | housekeeping defect escalation maintenance handoff | 1 | 1 | sop-global-defect-escalation | **PASS** |
| 5 | vehicle_service_due | When is vehicle VH-ESTATE-A-02 due for service? | 2 | 2 | estate-villa-fleet-eval | **PASS** |
| 6 | facility_work_order | pool filter replacement work order | 2 | 2 | estate-villa-facility-eval | **PASS** |
| 7 | estate_property_isolation | pool chemicals east shed (property_id=prop-villa-tuscany) | 2 | 2 | estate-villa-manual | **PASS** |
| 8 | unknown_insufficiency_teleporter | quantum teleporter array warranty (unknown) | 1 | ∅ | (insufficiency) | **PASS** |
| 9 | unknown_insufficiency_and_no_synthetic_policy | fictional underwater laser chandelier ZX9 (unknown) | 1 | ∅ | (insufficiency) | **PASS** |
| 10 | citation_lineage | guest arrival checklist security brief | 1 | 1 | sop-global-guest-arrival | **PASS** |

Coverage notes: cross-layer isolation enforced via layer= + forbid opposite layer; property scoping via property_id; two unknowns return insufficiency=true with empty hits; vehicle/facility cases cite evaluation-labeled Layer-2 summaries (not claimed as estate truth). Production FastEmbed+pgvector path is generated under pp/steward but **not** what served this oracle.


## Live remote verification (2026-07-21)

Base: `https://cerebrum-steward.onrender.com`  
Service id: `srv-d9fl136rnols73c72nf0` · Deploy: `dep-d9fl149hefhs73bbb6i0` (live)

| Probe | Result |
|-------|--------|
| `GET /health` | **200** `{ok:true, product_id:"cerebrum-steward", vertical:"estate"}` |
| `GET /v1/estate/demo` | **200** — 2 properties (Villa Aurora IT, Mayfair Residence GB), vendors, staff, work orders |
| `GET /v1/rag/dual` | **200** — indices populated (`steward_sop_v1` + `steward_estate_docs_v1`, 2 docs each after bootstrap) |
| `GET /v1/rag/query?q=guest+arrival` | **200** — top hit Layer 1 `sop-global-guest-arrival` score **0.314823** with citation |
| `GET /v1/rag/query?q=pool+chemicals&layer=2` | **200** — top hit Layer 2 `estate-villa-manual` score **0.35933** with citation |
| `POST /v1/rag/ingest` then query | **200** — ingest `verify-ingest-1`; subsequent Layer 2 query ranked it score **0.535578** |

Notes:

- Free-tier cold starts occasionally return edge **404** for 1–2 retries before **200**; treated as expected, not a product defect.
- Live `embedding_provider` is `local_feature_hash_v1` (honest demo path). Production FastEmbed + pgvector is **not** certified here.
- Render filesystem is ephemeral — ingest round-trip proves the API; durable store needs Postgres (`#73`).

---

## What the Factory generated well

- Expanded estate capability plan via dual-registered blocks
- `/product-dna/` with verifying `checksum_manifest.json`
- Resident runtime (`/v1/resident/status`, `/observe`) against product DNA
- Demo fixtures: villa + apartment, vendors, staff, maintenance calendar
- Dual RAG query returning **cited** Layer 1 / Layer 2 hits
- Honest IoT / smart-home connectors (`STATUS = "not_implemented"`)
- Deploy packaging (`Dockerfile` / `Procfile` / `render.yaml`) emitted by Factory
- Live sibling + Render path without hand-editing the product tree

## Where it needed help (honest)

1. **CI dual-registry:** Platform blocks vendor-mirrored for CI without Cerebrum-Blocks (`#70`). Canonical upstream remains Blocks.
2. **`.gitignore` trap:** Root/backend `storage` ignore hid the `storage` block mirror until force-tracked.
3. **Resident product inject lag:** Ship template omitted `/status` until `#71`.
4. **Sibling create credentials:** Agent cannot `createRepository` (GitHub App 403). Owner pre-created empty private repo; push-only thereafter.
5. **Process miss on `#69`:** Merged while Backend was red; corrected with `#70`. Do not repeat.
6. **Production RAG packs:** Still issue `#73` (Postgres 16 + pgvector, FastEmbed, governed packs, isolation oracles).

---

## Resident verification evidence (local generated product)

```
GET /health → 200 { ok, product_id=cerebrum-steward }
GET /v1/resident/status → 200 { enabled: true, mode: resident, ... }
GET /v1/resident/observe → 200 { level, dna_refs, health, ... }
product-dna checksum_manifest → 0 errors
GET /v1/estate/demo → 2 properties, vendors, staff, work orders
GET /v1/rag/query?q=arrival → hit_count ≥ 1 with citation.source_id
```

Flag: `RESIDENT_ENGINEER_ENABLED=true` for verification (defaults OFF in shipping config).

---

## Gaps (not hidden)

| Gap | Blocks full mission? | Close path |
|-----|----------------------|------------|
| Production embeddings / pgvector / packs | Yes for “production RAG” | Issue `#73` — Factory templates + regenerate |
| Estate block implementations are thin adapters | Partial | Deeper block logic in Cerebrum-Blocks |
| Free-tier cold start / ephemeral disk | Ops | Paid instance + Postgres for durability |
| `#69` merged red | Process (closed) | Standing order: merge only when Backend green |

---

## Acceptance checklist

| Criterion | Status |
|-----------|--------|
| Estate kit in Factory (`blueprints/` + shelf + fixtures) | Done (`#69`/`#70`) |
| Factory-generated Steward with DNA + Resident | Done |
| `bopoadz-del/Cerebrum-Steward` exists on GitHub | **Done** (owner-created; push `cf299d9`) |
| Deployed on Render | **Done** — https://cerebrum-steward.onrender.com |
| Dual RAG with citations (live) | **Done** (fixture / feature-hash path) |
| Production RAG packs (pgvector + FastEmbed) | **Open — `#73`** |
| Certification report with timestamps + honest % | This file |
| Factory tests green | CI green on `#70`/`#71`/`#74` |
| No hand-patches in product | Confirmed |

---

## Operator notes

```bash
# Regenerate (Factory machine)
cd backend && PYTHONPATH=. python -m app.factory.cli generate \
  --blueprint ../blueprints/steward/steward.v1.yaml \
  --out ../factory_outputs/Cerebrum-Steward \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT"

# Push only — never gh repo create (sibling already exists)
# See docs/provenance/pending_sibling_pushes/Cerebrum-Steward/push_steward_sibling.sh
```

Repo description law: **FACTORY-GENERATED — do not hand-edit; changes go through the Factory.**
