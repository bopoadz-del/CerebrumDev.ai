# STORE_REGRADE — STEP 0 baseline (BEFORE)

- **Tip date:** 2026-09-08
- **Live tip note:** `0031a67`
- **Scope:** measurement only — nine Store-green pilots; **no** `acceptance.py` implementation; **no** Store-gate change
- **Boot method:** uvicorn in shared venv (docker not available on box); host ports 18001–18009
- **Golden zip:** downloaded authenticated GET `/v1/sessions/sess_4591d5cc45d04fe1/product/package` → 622282 bytes

## BEFORE — pre-change baseline

This section is the pre-change baseline before any acceptance stamp work.

| session_id | product | zip_bytes | first_cap | no_token_POST | missing_field_POST | GET_/ | RAG | notes |
|---|---|---:|---|---|---|---|---|---|
| sess_7ca4ceb151ae48e9 | RetailHub | 662699 | `product_inventory_management` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18001; B=empty_body_missing_required |
| sess_d10dfc2890f7487b | InsureDistribute | 560024 | `workflow` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18002; B=empty_body_missing_required |
| sess_593357a0e1e54078 | Estate Steward / estate-management | 483170 | `estate_registry` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18003; B=empty_body_missing_required |
| sess_baecd2e879ca49a9 | FinanceOps FinFlow | 300558 | `automated_account_reconciliation` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18004; B=empty_body_missing_required |
| sess_5782f2264e0e4ff4 | cerebrum-steward founding | 436098 | `estate_registry` | 200 | 200 | 404 | empty | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18005; rag_caps=['dual_rag_sop', 'dual_rag_estate_docs']; B=empty_body_missing_required |
| sess_cec9a1345b2049bb | VetCare | 596988 | `vetcare_hub_veterinary_core` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18006; B=empty_body_missing_required |
| sess_fe80bf177a8545e6 | residential-lettings (old) | 439019 | `unit_registry_and_vacancy_tracking` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18007; B=empty_body_missing_required |
| sess_4591d5cc45d04fe1 | residential-lettings golden (new unlocked) | 622282 | `unit_registry_and_vacancy_tracking` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18008; B=empty_body_missing_required |
| sess_1bb2f36be7004ff4 | residential-lettings fresh GENERATE | 555916 | `unit_registry_and_vacancy_tracking` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18009; B=empty_body_missing_required |

## Summary — expected vs observed

Owner expected on **unfixed** stamps: **`200 / 200 / 404 / empty`** (RAG column `empty` when a RAG surface exists; otherwise no RAG).

| Probe | Expected (unfixed) | Observed (9/9) |
|---|---|---|
| A no_token_POST `/v1/<first_cap>` `{}` | 200 | **200** on all nine (JSON `ok:false` Missing required field — not HTTP 401) |
| B missing_field_POST | 200 | **200** on all nine (empty body / missing required; no auth gate on capability POST) |
| C GET `/` | 404 | **404** on all nine (frontend present in zip but not mounted at `/`) |
| D RAG | empty (or n/a) | Steward (`sess_5782f226…`): **empty** via `dual_rag_sop` plant+query; other eight: **n/a-no-rag** |

**Pattern match:** observed matches the expected unfixed signature (`200 / 200 / 404 / empty|n/a-no-rag`) across all nine pilots. No pilot claimed D1–D7 pass.

## Method notes

- `first_cap` taken from `docs/coder_receipt.json` → `capabilities[0]`.
- Zips staged under `/workspace/store_regrade_step0/zips/`; work trees under `/workspace/store_regrade_step0/work/<session_id>/`.
- Per-pilot JSON evidence: `/workspace/store_regrade_step0/logs/<session_id>.result.json`.
- Processes stopped after probes; no acceptance stamp code changed.

## Pilot zip sources

| session_id | zip |
|---|---|
| sess_7ca4ceb151ae48e9 | launching_ready_1137_cycle/retail-sess_7ca4ceb1.zip (662699) |
| sess_d10dfc2890f7487b | launching_ready_1137_cycle/insure-sess_d10dfc28.zip (560024) |
| sess_593357a0e1e54078 | launching_ready_1137_cycle/estate-sess_593357a0.zip (483170) |
| sess_baecd2e879ca49a9 | launching_ready_1137_cycle/finance-sess_baecd2e879ca49a9.zip (300558) |
| sess_5782f2264e0e4ff4 | cerebrum-steward-sess_5782f226-founding-pilot.zip (436098) |
| sess_cec9a1345b2049bb | launching_ready_1137_cycle/vetcare-sess_cec9a134.zip (596988) |
| sess_fe80bf177a8545e6 | launching_ready_1206_cycle/zips/fe80bf177a85-sess_fe80bf177a8.zip (439019) |
| sess_4591d5cc45d04fe1 | API product/package download (622282) |
| sess_1bb2f36be7004ff4 | lettings_regen_997c0d8/residential-lettings-sess_1bb2f36be7004ff4.zip (555916) |
