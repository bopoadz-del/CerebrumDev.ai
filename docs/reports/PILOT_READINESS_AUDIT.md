# CerebrumDev.ai — Pilot Readiness Audit

**Date (UTC):** 2026-07-21  
**Auditor scope:** Factory repo `bopoadz-del/CerebrumDev.ai` + live Steward product  
**Live URL:** https://cerebrum-steward.onrender.com  
**Product sibling:** https://github.com/bopoadz-del/Cerebrum-Steward (`main` @ `d30e7e5`, FACTORY-GENERATED)  
**Factory tip audited:** `0769c17` (merge of [#77](https://github.com/bopoadz-del/CerebrumDev.ai/pull/77))  
**Primary sources:** [`docs/factory/STEWARD_CERTIFICATION.md`](../factory/STEWARD_CERTIFICATION.md), [`docs/factory/certification-runs/steward-live-oracle-20260721.md`](../factory/certification-runs/steward-live-oracle-20260721.md), live probes this audit, CI run [29828612498](https://github.com/bopoadz-del/CerebrumDev.ai/actions/runs/29828612498)

---

## Verdict

### **CONDITIONAL** — controlled technical / demo pilot only

| Field | Value |
|-------|-------|
| **Verdict** | **CONDITIONAL** |
| **Confidence** | **68%** |
| **Ready for** | Owner-supervised demo, Factory proof, dual-RAG behaviour showcase on fixture data |
| **Not ready for** | Paying estate customer expecting durable semantic RAG, real UI, IoT/CMMS, or multi-tenant production isolation on live infra |

Factory generation → sibling publish → live Render verify is **certified**. Live dual-RAG **oracle PASS 10/10** on the honest demo path (`local_feature_hash_v1`). Production Postgres/pgvector + FastEmbed packs are **generated into the product tree** (`app/steward`) but **not what serves** `/v1/rag/*` on Render today.

---

## Executive scorecard

| Dimension | Score | Notes |
|-----------|-------|-------|
| 1. Factory path | **Pass** | Blueprint → plan → generate → dual registry; 0% hand-patch in product |
| 2. Steward product surfaces | **Partial** | Estate demo + dual RAG + Resident package live; UI stubs; connectors `not_implemented` |
| 3. Live deploy | **Pass (demo)** | Health 200; free-tier cold start; feature-hash embeddings; ephemeral disk |
| 4. Oracle / certification | **Pass (scoped)** | 10/10 live oracle; isolation + insufficiency certified on demo path only |
| 5. Tests / CI | **Pass** | Master tip CI green (Backend, Frontend, Docker) |
| 6. Gaps / pilot risks | **Material** | See §6 — block real-customer pilot until closed or contractually scoped out |

---

## 1. Factory path

**Evidence**

- Golden blueprint: [`blueprints/steward/steward.v1.yaml`](../../blueprints/steward/steward.v1.yaml)
- Kit: [`backend/app/factory/kits/private_estate_operations/`](../../backend/app/factory/kits/private_estate_operations/)
- Generator emits estate kit + demo RAG + production `app/steward` copy: `ProductGenerator._write_estate_kit_surfaces` in [`backend/app/factory/generator.py`](../../backend/app/factory/generator.py)
- Dual registry fail-closed: tests in `backend/tests/factory/test_dual_registry_and_planner.py`, `test_estate_kit_phase0.py`
- Certification claim: **0% hand-patches** inside product repo; sibling description law: *FACTORY-GENERATED — do not hand-edit* ([`STEWARD_CERTIFICATION.md`](../factory/STEWARD_CERTIFICATION.md))
- Pipeline APIs (Factory): `POST /v1/factory/product/{draft,plan,generate}` per [`AGENTS.md`](../../AGENTS.md)

**Assessment:** Factory path is the real generation path for Steward. Allowed Factory-side kit/template work is distinct from product hand-patching and is documented honestly (~98% product tree Factory-emitted; ~92% end-to-end mission including deploy per certification).

**Residual process risk:** PR [#69](https://github.com/bopoadz-del/CerebrumDev.ai/pull/69) was merged while Backend CI was red (corrected by [#70](https://github.com/bopoadz-del/CerebrumDev.ai/pull/70)). Standing orders now require merge-only-when-green ([`STANDING_ORDERS.md`](../resident-engineer/STANDING_ORDERS.md)).

---

## 2. Steward product surfaces

### Live (re-probed 2026-07-21 ~12:36Z)

| Surface | Result |
|---------|--------|
| `GET /health` | **200** `{ok:true, product_id:"cerebrum-steward", vertical:"estate"}` |
| `GET /v1/estate/demo` | **200** — 2 properties (Villa Aurora IT, Mayfair Residence GB), vendors, staff, assets |
| `GET /v1/rag/dual` | **200** — `embedding_provider: local_feature_hash_v1`; honesty string documents demo vs production path |
| `GET /v1/rag/query?q=guest+arrival` | **200** — Layer 1 hit `sop-global-guest-arrival` score ~0.315 with citation |
| `GET /v1/resident/status` | **200** — `enabled:false` (feature-flag default OFF); allowlisted heal actions present |

### Product composition (Factory-generated)

| Area | State |
|------|-------|
| Estate demo fixtures | Present (`fixtures/demo_estate.json` → generated data) |
| Dual RAG ingest/query/citations | Live on demo JSONL + `local_feature_hash_v1` |
| Production RAG runtime | Emitted as `app/steward` (Postgres/pgvector, FastEmbed, packs, migrations) — **not wired as default live path** |
| Resident Engineer | Packaged; status endpoint live; **disabled by default** |
| Connectors | Honest stubs: `cmms_stub`, `iot_stub`, `document_vault_stub`, `smart_home_placeholder` → `STATUS = "not_implemented"` ([generator `_write_connectors`](../../backend/app/factory/generator.py), kit README) |
| UI modules | Generated health-only stubs listing capability IDs (`_write_ui_stub`) — not an ops UI |

---

## 3. Live deploy

| Claim | Evidence | Honest reading |
|-------|----------|----------------|
| Render service live | Certification: `srv-d9fl136rnols73c72nf0`, deploy `dep-d9fl149hefhs73bbb6i0`; URL responds 200 after cold start | **True** |
| Embedding = bge-small / FastEmbed | Live `/v1/rag/dual` reports `local_feature_hash_v1`; oracle artifact same | **False for live** — production FastEmbed is template/code only until env + Postgres |
| Free-tier cold start | First probe in this audit waited ~35s for `/health`; certification notes edge 404 then 200 | **Expected ops friction** |
| Durable RAG indices | Render ephemeral filesystem; indices under demo JSONL / `data/rag/` | **Lost on restart** — ingest is API-proof only |
| Production path env | Honesty on `/v1/rag/dual`: set `STEWARD_DATABASE_URL`, `STEWARD_EMBED_BACKEND=fastembed`; `STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1` fails closed | **Not configured on free live service** |

Config defaults in Factory kit confirm hash is the safe default (`STEWARD_EMBED_BACKEND` default `"hash"` in [`steward_runtime/config.py`](../../backend/app/factory/kits/private_estate_operations/steward_runtime/config.py)).

---

## 4. Oracle / certification — what is certified vs not

### Certified (2026-07-21)

Live oracle suite `steward_live_oracle_v1` — **PASS 10/10**  
Artifact: [`docs/factory/certification-runs/steward-live-oracle-20260721.md`](../factory/certification-runs/steward-live-oracle-20260721.md)  
PR: [#77](https://github.com/bopoadz-del/CerebrumDev.ai/pull/77) (closes acceptance heart of [#73](https://github.com/bopoadz-del/CerebrumDev.ai/issues/73))

| Capability | Certified? |
|------------|------------|
| Layer 1 SOP retrieval + citations | Yes (feature-hash demo) |
| Layer 2 estate doc retrieval | Yes (feature-hash demo) |
| Cross-layer isolation (`layer=` forbid opposite) | Yes |
| Property scoping (`property_id`) | Yes |
| Unknown → `insufficiency` / empty hits | Yes (2 cases) |
| Citation lineage | Yes |
| Factory generate → sibling push → Render | Yes ([`STEWARD_CERTIFICATION.md`](../factory/STEWARD_CERTIFICATION.md)) |
| No product hand-edit | Yes (doctrine + certification) |

### Not certified

| Capability | Status |
|------------|--------|
| FastEmbed `BAAI/bge-small-en-v1.5` on live | **Not serving** |
| Postgres 16 + pgvector hybrid RRF on live | **Not deployed** |
| Durable multi-tenant production isolation under load | **Code/templates only** |
| Full pack acquisition (`full` profile, HF/Kaggle) | Mini fixtures / generated loaders; live uses demo corpus |
| Real IoT / CMMS / smart-home | Explicitly `not_implemented` |
| Principal-facing UI / staff ops UI | Stubs only |
| Resident L2 heal in production | Flag OFF; not pilot-operated |

Issue [#73](https://github.com/bopoadz-del/CerebrumDev.ai/issues/73) is **CLOSED** after oracle acceptance; certification text still correctly states production embeddings/packs were **not** what served the oracle.

---

## 5. Tests / CI

| Check | Result |
|-------|--------|
| Master tip | `0769c17` Merge PR #77 |
| CI run | [29828612498](https://github.com/bopoadz-del/CerebrumDev.ai/actions/runs/29828612498) — **success** |
| Jobs | Backend (pytest) ✓ · Frontend (build + lint) ✓ · Production Docker build ✓ |
| Factory tests | Present under `backend/tests/factory/` (dual registry, estate kit, generate/regenerate, Resident, etc.) |
| Prior tip after #74 | Also green ([29823997217](https://github.com/bopoadz-del/CerebrumDev.ai/actions/runs/29823997217)) |

**Note:** PR #77 body mentioned intermittent Actions billing blocks on the account; the merge tip itself completed green. Treat billing as an **ops continuity risk**, not a current red tip.

---

## 6. Gaps / risks for a real pilot customer

Ranked by pilot impact:

1. **Ephemeral RAG + no live Postgres** — customer ingest / uploads do not survive Render restart; no durable estate corpus on the live demo.
2. **Feature-hash is not semantic search** — `local_feature_hash_v1` ([`rag/embeddings.py`](../../backend/app/factory/kits/private_estate_operations/rag/embeddings.py)) token-hashes into 384-d vectors. Oracle passes on fixture wording; real paraphrases / messy PDFs will underperform vs bge-small.
3. **UI stubs** — modules are regenerate-only health shells; not usable for estate managers or principals.
4. **Connectors / Blocks depth** — IoT/CMMS/smart-home honest stubs; estate Store blocks remain thin adapters (certification gap table).
5. **Free-tier cold start + spin-down** — multi-second to multi-minute first hits; unsuitable for “always-on” pilot SLAs without paid instance.
6. **Production RAG unused on live** — `app/steward` + pack loaders exist in sibling but default routes remain demo `/v1/rag/*`.
7. **Resident OFF** — observe/heal not part of the live pilot posture unless explicitly enabled and operated.
8. **CI/billing continuity** — historical Actions billing friction can block Factory iteration during a live pilot window.
9. **Sibling create permissions** — agents cannot `createRepository` (GitHub App 403); ops depends on pre-created repos (mitigated for Steward).
10. **No billing / tenancy / SSO product surface** — out of Factory certification scope; absent for commercial pilot.

---

## 7. Go / no-go criteria

### Go for **CONDITIONAL** pilot (demo / technical proof) when ALL hold

- [x] Factory regenerate path green; no hand-patch doctrine enforced
- [x] Live `/health` + estate demo + dual RAG query with citations
- [x] Live oracle 10/10 on disclosed embedding provider
- [x] Connectors labeled `not_implemented` (no fake IoT)
- [x] CI green on Factory master tip
- [x] Customer / stakeholders **explicitly accept** demo RAG, ephemeral storage, stub UI, free-tier latency

### No-go for **customer production pilot** until

- [ ] Paid Render (or equivalent) + **Postgres 16 + pgvector** provisioned and durable
- [ ] Live path uses `STEWARD_EMBED_BACKEND=fastembed` with `STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1` (no silent hash)
- [ ] Production oracle re-run **10/10** against FastEmbed + pgvector (not feature-hash)
- [ ] Estate corpus durability + backup story for Layer 2 uploads
- [ ] Minimum viable UI for the pilot persona (or agreed API-only pilot)
- [ ] Written scope excluding IoT/CMMS until Blocks deepen connectors
- [ ] Cold-start / SLA acceptable on paid tier

---

## Recommended next actions (priority)

1. **Scope the pilot in writing** — “Factory + dual-RAG demo on fixtures” vs “production estate knowledge ops.” Do not blur them.
2. **If production-path pilot:** provision Postgres/pgvector on Render, wire `STEWARD_*` env, load mini packs, re-run oracle against FastEmbed; update certification honestly.
3. **If demo-path pilot:** keep free tier; document cold start + ephemeral loss; freeze fixture corpus; do not promise semantic search quality.
4. **UI decision:** either generate a thin estate ops shell for the pilot persona or sell API/demo-only.
5. **Blocks deepening:** move estate registry/maintenance beyond thin adapters for any ops-loop pilot.
6. **Resident:** leave OFF unless a controlled L1 observe demo is scheduled with flag + audit.

---

## Evidence index

| Item | Link / path |
|------|-------------|
| Steward certification | `docs/factory/STEWARD_CERTIFICATION.md` |
| Live oracle 10/10 | `docs/factory/certification-runs/steward-live-oracle-20260721.md` |
| Dual RAG PR | https://github.com/bopoadz-del/CerebrumDev.ai/pull/74 |
| Production RAG + oracle PR | https://github.com/bopoadz-del/CerebrumDev.ai/pull/77 |
| Issue #73 (closed) | https://github.com/bopoadz-del/CerebrumDev.ai/issues/73 |
| Live service | https://cerebrum-steward.onrender.com |
| Sibling tip | `d30e7e5` on `bopoadz-del/Cerebrum-Steward` |
| CI on master tip | https://github.com/bopoadz-del/CerebrumDev.ai/actions/runs/29828612498 |
| Standing orders | `docs/resident-engineer/STANDING_ORDERS.md` |

---

*This report is evidence-based as of 2026-07-21. Re-audit after production RAG is actually live-serving before upgrading the verdict from CONDITIONAL.*
