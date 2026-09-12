# ROLESANDTASKS.md — MR. FINANCE handoff

> Living handoff for CerebrumDev Factory. Update this file whenever roles or open work change.
> Owner agent: **MR. FINANCE** (Grok Bot). Authoritative UI: Factory Floor (`https://www.cerebrum-dev.com/floor`).
> Last written: 2026-09-12 (Asia/Dubai).

---

## Role

**MR. FINANCE** owns CerebrumDev.ai Factory Floor **end-to-end**:

1. Collector → Cloner → Writer → test → post/deploy
2. Keep Floor UI healthy, clean, and live
3. Own the repos; **merge when CI/gates are all green** — do not ask CHADi for merge permission
4. Never ask CHADi for API keys — keys live on Render; choose best option and deploy yourself
5. Prefer **Cursor BA** (`cursor_ba_available: true`) for Factory coding — **do not use Kimi or DeepSeek** for Floor chat or Factory coder
6. Ship only **HotelOps-grade + Store 12/12** (HotelOps A–H, not 12/12 alone)
7. Max **2 repair attempts** after a broken fix; 3rd failure → park and report (no looping)
8. Ground to Factory repo settings (`docs/factory/MR_FINANCE_GROUNDING_AND_GATING.md`). Only cross CHADi-gated lines when CHADi says so *with him* (N2 delete, path-jail widen, unauthorized spend, market/billing smoke claims)

**Not** a post-Cloner handoff receiver only. Not CHADi-driven. Not another agent's lane.

AI Product Delivery Engineer is **retired** for this lane.

---

## Standing CHADi locks (2026-09-12)

| Lock | Detail |
| --- | --- |
| Floor primary UI | Users chat with MR. FINANCE on Floor; operate Collector/Cloner/Writer/testing/post from Floor |
| No Kimi / DeepSeek | Deleted from Factory; do not re-add |
| Cursor BA | Preferred coding path |
| Merge when green | No ask |
| Keys on Render | Never ask CHADi; never full-replace Render env (`replace:true`) |
| HotelOps bar | Every platform must pass HotelOps A–H + Store 12/12 |
| Domain handoff | GitHub `domain:*` + `handoff` and/or `DOMAIN_HANDOFF_WEBHOOK_URL` — expand beyond finance to car_dealership/automotive |
| BA path-lane | Option C Hybrid: pivot/cerebrum-builds BA may write `tests/**`; in-process Factory WRITER sealed off `tests/**` until N2 |

Canonical grounding: `docs/factory/MR_FINANCE_GROUNDING_AND_GATING.md` (PR #430 may still be open).

---

## Infrastructure

| Piece | ID / URL |
| --- | --- |
| Render workspace | `tea-d9rteq2jnfac738dnc70` |
| Factory backend | `srv-d9ta2pad0e5s738lllpg` (`cerebrumdev-backend`) |
| API health | `https://api.cerebrum-dev.com/health` |
| API ready | `https://api.cerebrum-dev.com/ready` |
| Floor UI | `https://www.cerebrum-dev.com/floor` |
| Platform repo | `https://github.com/bopoadz-del/CerebrumDev.ai` |
| Builds repo | `https://github.com/bopoadz-del/cerebrum-builds` |

### LLM / coder status after Kimi+DeepSeek delete (2026-09-12)

- Cleared on Render (merge, not replace): Kimi/DeepSeek/Moonshot-related keys and Moonshot `CEREBRUM_LLM_*` / chat paths
- `FACTORY_CODE_CLI=cursor`; on-box CLI `available:false`; **`cursor_ba_available: true`**
- `/ready`: `llm_configured: false` until non-Kimi Floor chat path is live
- Do **not** re-enable Kimi or DeepSeek to fix chat

---

## Routines (MR. FINANCE)

| Routine | Folder | Trigger | Intent |
| --- | --- | --- | --- |
| Factory domain handoff webhook | `factory-domain-handoff-webhook` | webhook | Take over after Collector+Cloner |
| Floor UI live + chat reachable | `floor-ui-live-chat-reachable` | `40 8-19 * * 1-5` (Dubai weekday hours) | Keep Floor healthy; quiet when green |

---

## Open / in-flight work

### P0 — Floor chat reachable (non-Kimi)
- PR #432 / agent `bc-6accd6be-63bd-4171-a3ff-691230d9a5bb` — prefer primary LLM over broken OpenRouter path
- Confirm `/ready` `llm_configured: true` and Floor chat works

### P1 — Domain handoff expand
- PR #431 / agent `bc-c7183f7c-e548-44f1-ab94-d40afab6e600` — expand beyond finance to car_dealership/automotive — **merge when green**

### P1 — Car dealership HotelOps repair
- Session `sess_033ba09e0d8b4212` tip historically `81dc799`
- cerebrum-builds PR #1 / agent `bc-8b36f331-e5b5-46f5-a4e7-3a897638918c` — HotelOps A+H repair 1/2 — **merge when green**

### P1 — FinanceOps HotelOps H
- Session `sess_48a72e0d1cac44ae` tip after builds PR #2 → `135f23c`
- cerebrum-builds PR #3 / agent `bc-1ddf5d17-2e4a-465e-bf17-dc4d371e9fe9` — HotelOps H repair 1/2

### P2 — Docs grounding
- CerebrumDev.ai PR #430 — merge when green

### Standing
- Keep Floor live; merge green PRs; never replace:true Render env; never restore Kimi/DeepSeek

---

## How a takeover agent should start

1. Read this file + grounding docs
2. GET /health + /ready — fix chat without Kimi/DeepSeek
3. Check open PRs on CerebrumDev.ai and cerebrum-builds; merge when green
4. Drive Floor via Floor automation / GitHub `domain:*`+`handoff` — not Grok SendToAgent as handoff trigger
5. Update this file before ending a long session

---

## Do not

- Ask CHADi for merge permission or API keys
- Full-replace Render environment variables
- Use Kimi or DeepSeek for Floor chat or Factory coder
- Claim market-ready / billing-ready without CHADi gate
- Widen path-jail or delete N2 without CHADi sign-off
- Loop repairs past 2 attempts
