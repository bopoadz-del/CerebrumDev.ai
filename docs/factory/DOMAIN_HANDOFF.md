# Domain handoff (any vertical → MR.FINANCE)

After **COLLECTOR + CLONER**, Factory delivers the frozen command to
MR.FINANCE **without** Grok Bot `SendToAgent`.

Every Floor session fires. Skip only when the handoff already ran
(idempotent) or there is no workspace. Unknown verticals still fire
with a derived slug (`domain:airline`, `domain:hotelops`,
`domain:retail`, …) or `domain:general`.

MR.FINANCE then produces, checks, posts, and deploys.

## Domain resolution

| Vertical aliases (product_id / vertical) | Domain id | GitHub label |
|------------------------------------------|-----------|--------------|
| `finance`, `finance_ops`, `finance-ops`, `financeops` | `finance` | `domain:finance` |
| `car_dealership`, `car-dealership`, `cardealership`, `automotive`, `auto_dealership`, `auto-dealership`, `autodealership`, `dealership` | `automotive` | `domain:automotive` |
| any other token (`airline-delivery-management`, `aviation`, `lettings`, `hotelops`, …) | derived slug | `domain:<slug>` |

Detection normalizes spaces / hyphens / underscores. Compound product
ids keep a short head (`airline-delivery-management` → `airline`).
Finance / automotive aliases keep dedicated label quality. Everything
else still posts.

Payload `domain` / `vertical` / instruction text match the resolved
domain. Finance instruction text stays the original finance wording.

## What is delivered

- `docs/coder_brief.md` (C-BRIEF; compiled at handoff if missing)
- Workspace path + session id
- Floor URL
- Instruction to take over (MR.FINANCE; names the resolved domain when it is not finance)

## Mechanism

1. GitHub issue on `CEREBRUM_BUILDS_REPO` (default `bopoadz-del/cerebrum-builds`)
   with labels `domain:<resolved>` + `handoff` (override repo with
   `DOMAIN_HANDOFF_GITHUB_REPO` if needed).
2. Local marker `docs/domain_handoff.json` (idempotent).
3. Optional POST to `DOMAIN_HANDOFF_WEBHOOK_URL` when set. Cursor
   automation webhooks require `Authorization: Bearer <crsr_…>` — set
   `DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION` (or `DOMAIN_HANDOFF_WEBHOOK_KEY`)
   in the Render dashboard. Never commit the value.

## Optional env

| Env | Required | Notes |
|-----|----------|-------|
| `DOMAIN_HANDOFF_WEBHOOK_URL` | no | POST handoff JSON after the issue. Set in Render dashboard only — agents must not full-replace Render env. |
| `DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION` | no | Dashboard-only secret. Sent as the `Authorization` header. Use `Bearer <token>` as-is, or a raw `crsr_…` / token (prefixed with `Bearer `). A value starting with `Authorization:` uses the part after the colon. Alias: `DOMAIN_HANDOFF_WEBHOOK_KEY`. |
| `DOMAIN_HANDOFF_GITHUB_REPO` | no | Override issue repo (`owner/name`). |
| `CEREBRUM_BUILDS_GITHUB_TOKEN` | yes for issues | Same token used for cerebrum-builds. |

## Wire

`run_cloner` → `handoff_after_cloner` (best-effort; never fails CLONER).

Module: `backend/app/factory/build/domain_handoff.py`
