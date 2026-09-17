# Phase 2 — status and parked work (overnight handoff)

Written 2026-09-17 ~08:00 local. Owner: codewhale session.

## Merged
- **§0.1** — RAG gate fix: PR #503, squash-merged to master (2026-09-17 02:21Z), CI green,
  zero builds running at merge time. `rag_roundtrip_hit` can no longer be satisfied by an echo.

## In flight: §0.2 (tenant isolation) — branch `fix/phase2-0.2-tenant-isolation`
IMPLEMENTED AND FUNCTIONALLY PROVEN, NOT YET MERGED.
- Emitter-guaranteed tenancy (`render_tenancy_module`), auth delegates to the single
  resolution path, kernel_bridge `product_context(request)`, tenant-scoped routes/store/
  migrations/domain_ops, factory-side `or session_id` fallbacks removed, 13th acceptance
  check `cross_tenant_404`, pivot workflow floor 13.
- Focused suites GREEN: test_store_acceptance, test_n3_store_gate, test_phase2_tenant_repro
  (live A-write / B-read → 404 in a fresh interpreter), test_deploy_scaffold (43 passed).
- Structural test locks the fix: `backend/tests/factory/test_phase2_tenant_repro.py`.

### THE WALL (why not merged)
Full factory suite: 102 failed / 1596 passed. Root cause: the required `tenant_id`
parameter on the emitted store functions collides with the deterministic
`_persist_record` handler envelope — handler bodies persist directly and can no
longer know the tenant. The coherent resolution is a persistence-contract
migration: **handlers return ok:true; the ROUTE persists via the tenant-scoped
`save(payload)` lambda** (the templated route body already does this).

Migration checklist (started, partially applied on the branch):
1. `persist_accept.py` — contract text already flipped ("handlers must not call
   store.save directly"). Remaining: `grounded_persist_assign()` (line ~406) still
   returns `"    stored = _persist_record(payload)"` → must become
   `"    stored = save(payload)"` (route scope).
2. `roles_handlers.py` — lines ~1242/1422/1453 `"    stored = _persist_record(payload)\n"`
   → `save(payload)`; remove the rendered `def _persist_record(...)` block (~1355);
   update the kept-text check at ~3422 (`_persist_record(` / `store.save(` →
   accept `save(payload)`).
3. `workflow_accept.py` line ~565 — same `_persist_record(payload)` → `save(payload)`.
4. `coder_session.py` ~2490 — comment only.
5. `coder.py` brief — already flipped to route-persists wording.
6. `brief_lint.py` — acceptance pattern already flipped.
7. Test expectations to update (byte-exact, use counted replaces):
   - `test_writer_behaviour_gate.py` lines 135, 254, 591, 671 (rendered save lambdas →
     add `, tenant_id=tenant.tenant_id`); 958/962, 1137/1141, 1148/1152, 1307/1311
     (fixture bodies `store.save(...)`/`store.list_all(...)` → `save(payload)`/`list_all()`).
   - `test_persist_accept_contract.py` lines 178/181 (contract needle → "tenant-scoped
     save(payload)"), 202 (`"_store.save(ENTITY, record)" in module` → drop/change when
     `_persist_record` is removed from the module), 239 (rendered lambda → tenant-aware),
     289 (fixture body → `save(payload)`).
   - `test_actions_packaging.py` 126/130 (packaged actions must NOT contain
     `store.save` anymore → assert absence + route persistence instead).
   - `test_data_lifecycle.py` 115-214 and `test_deploy_observe.py` 375-446 (rendered
     product-test strings now carry `tenant_id=TENANT`).
   - `test_cbrief_reuse_*.py` — factory-side `store.list_all(...)` calls need
     `tenant_id="local"`.
   - Then re-run: the failing writer_behaviour/cbrie/persist files first, then the
     full `tests/factory`; expect ~30-45 min.
8. When green: PR to master (one PR per section), CI, merge on green — CHECK NO
   BUILD RUNNING before merging (deploy kills in-flight builds).

## Not started (do NOT begin until §0.2 merges)
- **§1** — branch `fix/phase2-1-client-ingestion` (off §0.2). Done already:
  `chunker.chunk_document()` implemented (naive splitter, contract-exact,
  tenant refused-by-name); `router._require_product_tenant` wired to the product's
  tenancy; `deploy.render_main(product_name, vertical)` mounts the ingestion router
  gated on `inventory.vertical_is_excluded`. NOT VERIFIED (suite red while §0.2
  pending) and the mount needs a render test.
- **§2** Drive connector (reuse factory_drive mechanics; donor OAuth in the Cerebrum
  repo; sync_file → chunk_document(layer=2) → RetrievalEngine.ingest; content-asserted
  round-trip test).
- **§3** Formula intake (gate model: owner/effective/expiry/envelope/≥1 worked
  example; envelope refusal-not-extrapolation; per-tenant persistence on the Chroma
  isolation boundary; LLM drafts + code validates; precedence divergence record reuse).
- **§4** Acceptance checks (positive content-typed hit, negative control,
  cross-tenant retrieval empty, overlay refusal, envelope refusal) executed as
  rendered with RED-then-GREEN verbatim proofs.

## Housekeeping
- Local secrets: `CerebrumDev.ai/backend/.env` (gitignored) carries RENDER_API_KEY,
  CEREBRUM_DEV_API_KEY, CEREBRUM_API_URL/KEY, CEREBRUM_BUILDS_GITHUB_TOKEN.
- The deploy warning is real: auto-deploy on master; a merge kills in-flight builds.
