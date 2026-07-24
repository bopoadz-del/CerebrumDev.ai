# Registered-but-Dead Kernel Audit

**Repository:** `bopoadz-del/CerebrumDev.ai`  
**Branch:** `master`  
**Audit date:** 2026-07-24  
**Auditor:** Factory CI + registry meta-test (`backend/tests/test_registry_invariants.py`)  
**Rule:** No kernel may be claimed market-ready on deterministic/fallback behavior alone. Live evidence is required.

## Summary

| Kernel | Status | Evidence |
| --- | --- | --- |
| 1. Chat LLM (moonshot-v1-8k) | **LIVE** | Streamed reply echoing brief specifics; no fallback table |
| 2. LLM architect drafting | **LIVE** | `source:"drafted"`, multiple capabilities, block_ids populated from dual registry |
| 3. Planner fail-closed → generation | **LIVE** | Approve → generate → zip export with Dockerfile/render.yaml/product-agent |
| 4. Grounding law | **LIVE** | Refuses to invent download links or live URLs |
| 5. Cross-account isolation | **LIVE** | Foreign account → 404 on session + product |
| 6. Billing honesty | **LIVE (honest)** | Structured status; checkout → `503 stripe_not_configured` when Stripe unset |
| 7. Resident engineer | **PARKED by design** | `enabled:false`, maturity APPRENTICE, allowlisted heals declared |
| 8. Workbench / build mode | **PARKED by design** | `build_mode_enabled:false`, `kimi_workbench_enabled:false` |
| 9. Change-request intake | **PARKED by design** | `intake_enabled:false`, M3 paperwork only |
| 10. Redis rate limiting | **LIVE** since 2026-07-24 | Render Key Value `cerebrumdev-redis` provisioned; `/health` reports `redis.configured: true`, `redis.ok: true` |
| 11. Draft routing | **LIVE** after fix | LLM attempt now precedes deterministic steward fallback; `"estate"` no longer short-circuits LLM |
| 12. CI wiring | **EXISTS** | `.github/workflows/ci.yml` runs pytest + frontend build + docker build on push to master |
| 13. Audit kernel artifact | **LIVE** | This document + `test_registry_invariants.py` |
| 14. Registry meta-test | **LIVE** | `backend/tests/test_registry_invariants.py` asserts router mounts and honest disabled status |
| 15. AGENTS.md deploy gate | **WIRED** | See below |

## Classification definitions

- **LIVE** — verified against real services or behavior; deterministic success is not enough.
- **PARKED by design** — registered in code, intentionally gated OFF by default, status endpoint is honest.
- **PARKED** — registered but not configured in this environment; falls back safely.
- **STUB** — placeholder that refuses service or returns static honest empties.
- **UNVERIFIED** — no live evidence yet; cannot be claimed ready.

## Stop conditions

1. Any kernel whose status is **UNVERIFIED** cannot be listed as ready in release notes.
2. Any kernel whose status is **PARKED** must expose an honest status endpoint or 503.
3. Any kernel whose status is **LIVE** must have a reproducible evidence command or CI test.
4. Deterministic/fallback success is **never** accepted as evidence for **LIVE**.

## Evidence commands

- Registry invariants: `cd backend && ENV=test ./venv/bin/python -m pytest tests/test_registry_invariants.py -v`
- Full backend suite: `cd backend && ENV=test ./venv/bin/python -m pytest -q`
- Post-deploy smoke (requires live deployment + real LLM key):
  ```bash
  python3 scripts/post_deploy_smoke.py https://cerebrumdev-backend.onrender.com
  ```
  All checks must print `[LIVE]`.

## Deploy gate (also in AGENTS.md)

Before any market-ready claim:

1. Run `scripts/post_deploy_smoke.py` against the live URL.
2. Every kernel must print `[LIVE]`.
3. A deterministic/fallback pass is a failure.
4. Do not merge or tag a release until the smoke script exits 0.
