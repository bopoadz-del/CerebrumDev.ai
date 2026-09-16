# Store Inventory — verified enumeration

**Verified on 2026-09-16** by a programmatic scan of the Cerebrum-Blocks
checkout (`block_registry/`, `block_store/`, `block_store/kits/`), a
stub-pattern sweep, and a dedicated block-library audit. Every claim below
is from the files themselves, not from memory.

## Trust summary

- **105 registry blocks**, of which **97 hand-authored ("Cerebrum Team")**
  and 8 without a manifest.
- **Zero stub-like blocks in the Store registry.** The echo-stub pattern
  (`run()` returning `{"status": "ok", "result": <input>}` unconditionally)
  appears nowhere in `block_registry/`. The universal_kernel security wave
  (audit_evidence, provenance_verification) contains real SHA-256 chain
  verification with tamper tests.
- **5 stubs exist OUTSIDE the Store** — in the factory vendor mirror
  (`backend/app/factory/vendor_blocks_mirror/`), pinned by `blocks.lock.json`
  as `factory-vendor-mirror` and wired into the estate/Steward line:
  `evidence_verifier`, `estate_registry`, `readiness_engine`,
  `estate_maintenance`, `portfolio_rollup`. A queued patch would register
  them into the Store; until they are implemented (in progress) they must
  not ship as certified.

## Vertical readiness

### Ready — build and test these five

| Vertical | Kit | Domain blocks (beyond the generic core) |
|---|---|---|
| hotels | hotel_management | hotel_v2 |
| insurance | insurance | insurance_v2, agency_hierarchy, producer_record, agency_commission_engine, channel_router, attrition_scorer, incentive_targeting, hkia_gn16_rules, bordereaux_ingest, distribution_analytics |
| construction | construction | construction_v2, boq_processor, spec_analyzer, sympy_reasoning, drawing_qto, primavera_parser, smart_orchestrator, jetson_gateway, bim_extractor, bim, learning_engine, recommendation_template, project_reasoner |
| finance | finance, finance_ops | finance_v2, finance_canonical_model, finance_import, finance_data_quality, finance_reconciliation, finance_coa_governance, finance_saas_metrics |
| retail | retail | retail_v2 |

The generic core shared by domain kits: `pdf, ocr, chat, image,
formula_executor, formula_executor_v2`. A capability that resolves only to
these (or to cross-cutting plumbing) is flagged as a domain gap at plan
time (2b).

### Excluded — no testing (declared)

medical, legal, veterinary, pharma. Drafts for these verticals carry the
honest note: no authoritative domain content; domain logic would be
fabricated.

### Unverified — kit exists, not on the ready list

agriculture, automotive, aviation, education, hr, manufacturing,
mep_coordination, oil_gas, real_estate, supply_chain. Their kits exist in
the Store; depth is not guaranteed until each is declared ready.

## Full kit list (22)

_template (empty) · agriculture · automotive · aviation · construction ·
education · finance · finance_ops · hotel_management · hr · insurance ·
legal · manufacturing · medical · mep_coordination (geometry_engine,
clearance_rules, clash_triage, clash_resolver, bcf_export, version_diff,
model_clone) · oil_gas · pharma · real_estate · retail · supply_chain ·
universal_business (empty) · universal_kernel (identity,
authorization_policy, scope_guard, rate_limit_guard, audit_evidence,
provenance_verification, secure_ingestion, document_parsing, durable_jobs,
embedding_provider, vector_store, hybrid_retrieval, llm_provider,
grounded_answer, xlsx_export, pdf_export, json_audit_export, health,
monitoring, structured_outcomes, billing_entitlement, notification_mailer,
approval_action, block_runner)

## Registry blocks (105) — categories

- **Domain blocks**: one `*_v2` per vertical kit (retail_v2, hotel_v2,
  insurance_v2, finance_v2, construction_v2, ...) plus the construction
  and insurance specialist sets listed above.
- **Generic core**: pdf, ocr, chat, image, formula_executor(_v2).
- **Cross-cutting plumbing**: database, storage, queue, workflow,
  notification, team, validation, audit, dashboard, analytics, event_bus,
  file_hasher, document_engine, capture, knowledge, memory, search, web,
  email, auth, rate_limiter, cache_manager, monitoring, secrets, skills,
  review, sandbox, orchestration/smart_orchestrator.
- **Governance & security** (universal_kernel): identity,
  authorization_policy, scope_guard, rate_limit_guard, audit_evidence
  (SHA-256 chain — REAL), provenance_verification (digest/root-hash — REAL).
- **Capture/parsing**: drawing_qto, bim_extractor, bordereaux_ingest,
  primavera_parser, spec_analyzer, ocr(_v2), pdf(_v2), image,
  video_metadata_ingest, hkia_gn16_rules.
- **Specialist**: jetson_gateway, sympy_reasoning, learning_engine,
  recommendation_template, project_reasoner, attrition_scorer,
  incentive_targeting, distribution_analytics, agency_commission_engine,
  producer_record, channel_router, finance_canonical_model and the
  finance_ops quality set, mep_coordination clash stack.

## Known defects (tracked, not hidden)

1. **5 estate stubs in the factory vendor mirror** — implementation in
   progress; do not trust evidence/registry/readiness features in the
   estate line until fixed.
2. `block_store/admin_block.py::_preflight` reports `database: ok` without
   probing a database (PARTIAL — Store side, one-line fix queued).

## Consumers of this file

- The factory inventory declaration (`app.factory.inventory`) — ready
  verticals and kit domain sets are mirrored there and enforced at draft
  and plan time.
- Build/test planning: only the five ready verticals are tested until more
  kits are declared ready.
