# Steward Factory Certification Report

**Product:** Cerebrum Steward — Remote Asset & Estate Operations Platform  
**Factory repo:** `bopoadz-del/CerebrumDev.ai`  
**Intended product repo:** `bopoadz-del/Cerebrum-Steward`  
**Run log:** `docs/factory/certification-runs/steward-20260721T080317Z.log.jsonl`  
**Factory tip at final generate:** `bfa74b18e052496c7022f1f568b24c2825d2b880`

---

## Verdict

**FACTORY CERTIFIED — generation & local verification**  
**NOT CERTIFIED — live sibling deploy** (blocked; see gaps)

The Factory built the Steward end-to-end from the estate kit blueprint: Product DNA
(15 files + checksums), Resident runtime, demo fixtures, dual RAG surfaces, and
deploy packaging (`Dockerfile` / `Procfile` / `render.yaml`). Local verification
against the generated tree passed.

Live deployment to Render and publication of `bopoadz-del/Cerebrum-Steward` did
**not** complete in this run: the agent GitHub token returns
`403 Resource not accessible by integration` on `createRepository`. No hand-patch
of product code was performed. Push kit for the owner:
`docs/provenance/pending_sibling_pushes/Cerebrum-Steward/`.

---

## Timeline (UTC)

| Stage | Timestamp | Notes |
|-------|-----------|-------|
| Certification run start | `2026-07-21T08:03:17Z` | Phase 1 |
| Generate start (CLI) | `2026-07-21T08:03:17Z` | `app.factory.cli generate` + golden blueprint |
| Generate end | `2026-07-21T08:03:18Z` | ~1s wall clock for packager emit |
| Local verify (first pass) | `2026-07-21T08:03:30Z`–`08:03:31Z` | Estate demo + RAG; Resident `/status` missing |
| GitHub repo create attempt | `2026-07-21T08:03:31Z` | **403** — integration cannot create org/user repos |
| Factory upgrade: Resident `/status` + packaging | merged `#71` @ `2026-07-21T08:06:59Z` | Upgrade Factory, re-generate (doctrine) |
| Regenerate after inject | `2026-07-21T08:05:23Z` | Resident status + observe + DNA checksum OK |
| Final generate on master tip | ~`2026-07-21T08:07:Z` | Align proof with `#71` |
| Report written | `2026-07-21T08:07:Z` | This document |

**Elapsed (kit → generated artifact):** Phase 0 kit PRs spanned ~`07:55`–`08:07` UTC including CI fixes; pure generate is sub-second once the Factory tip is green.

### Phase 0 prerequisites (Factory-side — only manual work allowed)

| PR | Result |
|----|--------|
| [#69](https://github.com/bopoadz-del/CerebrumDev.ai/pull/69) estate kit blueprint + shelf | Merged; Backend was red in CI (no Blocks checkout) — **process miss: merged before green** |
| [#70](https://github.com/bopoadz-del/CerebrumDev.ai/pull/70) vendor-mirror platform blocks + `storage/` gitignore fix | Merged **when green** |
| [#71](https://github.com/bopoadz-del/CerebrumDev.ai/pull/71) Resident `/status` inject + runtime packaging | Merged when green |

---

## Generated vs hand-written

| Category | Estimate | Justification |
|----------|----------|----------------|
| **Factory-generated product tree** | **~98%** of product files | `ProductGenerator` emit: actions, hats, workflows, UI stubs, connectors, DNA, Resident, estate kit router, fixtures, Dockerfile/Procfile |
| **Factory-side kit work (Phase 0)** | Manual in Factory repo only | Blueprint YAML, shelf entries, vendor mirrors, demo fixture JSON, generator templates — **allowed** by doctrine |
| **Hand-patches inside product repo** | **0%** | No product files edited after generate; gaps closed by regenerating |
| **Human / owner actions still required** | Deploy gate | Create `bopoadz-del/Cerebrum-Steward` + push (`push_steward_sibling.sh`) + connect Render |

**Honest percentage for “product code written by Factory packager”:** **100% of the generated tree** (227 files at certification proof).  
**Honest percentage for “end-to-end Factory mission including live deploy”:** **~75%** — generation + verify done; sibling GitHub + Render URL not live.

---

## What the Factory generated well

- Expanded estate capability plan (registry, SOP, vendors, maintenance, staff, principal dashboard, onboarding, dual RAG) via dual-registered blocks
- `/product-dna/` complete with verifying `checksum_manifest.json`
- Resident runtime injected; after `#71`, `/v1/resident/status` + L1 `/observe` work against product DNA
- Demo fixtures: 1 villa (IT) + 1 apartment (GB), vendors, staff, maintenance calendar
- Dual RAG query returning **cited** Layer 1 / Layer 2 hits from fixture corpora
- Honest IoT / smart-home connectors (`STATUS = "not_implemented"`)
- Deploy packaging emitted by Factory (not hand-added to product)

## Where it needed help (honest)

1. **CI dual-registry:** Platform blocks had to be vendor-mirrored for CI without Cerebrum-Blocks (`#70`). Canonical upstream remains Blocks.
2. **`.gitignore` trap:** Root/backend `storage` ignore hid the `storage` block mirror until force-tracked — caused false UNSUPPORTED on `estate_registry` in CI.
3. **Resident product inject lag:** Factory `/status` existed but ship template omitted it until `#71`.
4. **Sibling repo / deploy credentials:** Agent cannot create `bopoadz-del/Cerebrum-Steward` (GitHub App 403). Render MCP web service also requires a cloneable Git URL.
5. **UI-first path:** This run used Factory CLI generation (deterministic equivalent of Design Product → generate). Kit configurator chain-approve Deploy panel was not exercised live because sibling `DEPLOY_REPO_URL` / repo did not exist.
6. **Process miss on `#69`:** Merged while Backend was red; corrected immediately with `#70`. Do not repeat.

---

## Resident verification evidence (local generated product)

```
GET /health → 200 { ok, product_id=cerebrum-steward }
GET /v1/resident/status → 200 { enabled: true, mode: resident, allowlisted_heal_actions: [...] }
GET /v1/resident/observe → 200 { level, dna_refs, health, block_versions, ... }
product-dna checksum_manifest → 0 errors
GET /v1/estate/demo → 2 properties, vendors, staff, work orders
GET /v1/rag/query?q=arrival → hit_count ≥ 1 with citation.source_id
```

Flag: `RESIDENT_ENGINEER_ENABLED=true` for verification (defaults OFF in shipping config).

---

## Gaps (not hidden)

| Gap | Blocks full mission? | Close path |
|-----|----------------------|------------|
| `bopoadz-del/Cerebrum-Steward` missing on GitHub | Yes for “deployed product” acceptance | Owner creates private repo; run `docs/provenance/pending_sibling_pushes/Cerebrum-Steward/push_steward_sibling.sh` |
| No live Render URL for Steward | Yes for Phase 2 remote verify | After push, Blueprint/deploy from `render.yaml` or Deploy panel with `DEPLOY_REPO_URL` |
| Dual RAG is fixture-backed | Partial | Production embeddings need live `knowledge`/`vector_search` runtime wiring |
| Estate block implementations are thin adapters | Partial | Deeper block logic belongs in Cerebrum-Blocks (clone-only from Factory) |
| `#69` merged red | Process | Standing order: merge only when Backend green (`#70`/`#71` complied) |

---

## Acceptance checklist

| Criterion | Status |
|-----------|--------|
| Estate kit in Factory (`blueprints/` + shelf + fixtures) | Done (`#69`/`#70`) |
| Factory-generated Steward with DNA + Resident | Done (local `factory_outputs/Cerebrum-Steward`) |
| `bopoadz-del/Cerebrum-Steward` exists on GitHub | **Pending owner** |
| Deployed on Render | **Pending sibling repo** |
| Resident live on deployed URL | **Pending deploy** |
| Dual RAG with citations | Done locally |
| Certification report with timestamps + honest % | This file |
| Factory tests green | 413 passed (+ kit/inject); CI green on `#70`/`#71` |
| No hand-patches in product | Confirmed |

---

## Owner next step (5 minutes)

```bash
# 1. Create private repo bopoadz-del/Cerebrum-Steward in GitHub UI (or with an org admin token)
# 2. From a machine with push rights and a fresh Factory generate:
./docs/provenance/pending_sibling_pushes/Cerebrum-Steward/push_steward_sibling.sh
# 3. Connect Render to that repo (render.yaml included) with RESIDENT_ENGINEER_ENABLED=true
```

Then re-run Phase 2 against the live URL and amend this report’s verdict to full **FACTORY CERTIFIED**.
