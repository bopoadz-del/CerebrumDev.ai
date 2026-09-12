# Domain handoff (FinanceOps → MR.FINANCE)

After **COLLECTOR + CLONER** on a finance vertical (`finance_ops` /
`finance-ops`), Factory delivers the frozen command to MR.FINANCE
**without** Grok Bot `SendToAgent`.

MR.FINANCE then produces, checks, posts, and deploys.

## What is delivered

- `docs/coder_brief.md` (C-BRIEF; compiled at handoff if missing)
- Workspace path + session id
- Floor URL
- Instruction to take over

## Mechanism

1. GitHub issue on `CEREBRUM_BUILDS_REPO` (default `bopoadz-del/cerebrum-builds`)
   with labels `domain:finance` + `handoff` (override repo with
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
