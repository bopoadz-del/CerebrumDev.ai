# Factory CLI-pivot — cerebrum-builds store gate (G)

Drop-in workflow for the **private `cerebrum-builds` store** once that repo exists
(owner click — this PR does not create it). Copy
[`cerebrum-builds-store-gate.yml`](cerebrum-builds-store-gate.yml) to
`.github/workflows/store-gate.yml` in that repo.

## What it does

On `workflow_dispatch` or push to `build/**` / `builds/**` / `artifact/**`:

1. `docker build` the product artifact.
2. Run `scripts/acceptance.py` **inside** that image (not on the runner's host Python).
3. Parse the 12-line floor and write **k/12** back as a workflow artifact
   (`store_gate.json`) plus a commit status.

## 12-line floor (N3 names)

`no_token_401`, `missing_field_422`, `enum_422`, `ui_served_200`,
`rag_roundtrip_hit`, `single_persistence_root`, `ci_present_full_suite`,
`handler_bodies_distinct`, `health_fail_closed`, `openapi_committed`,
`docker_health_200`, `authorship==receipt`.

k/12 of those named lines is the only green. A clean Factory receipt+diff is
**not** this gate — N1b only hands off here.

G-floor names above are canonical (`ci_present_full_suite`,
`authorship==receipt`). Factory `store_acceptance.ACCEPTANCE_CHECK_NAMES`
still uses the pre-G aliases (`ci_present_and_full_suite`,
`authorship_floor`); that self-grade is not rewritten in this PR.

## Generate / Continue

Live Floor Generate and Continue call `run_cli_pivot` on the COLLECTOR+CLONER
workspace when Cursor executor keys are present (`CURSOR_API_KEY` /
`CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY`). Keys-present is the gate
(no extra `FACTORY_CLI_PIVOT` flag). Absent keys keep the in-process WRITER
path until N2. A `HANDOFF_TO_N3` receipt is a ledger note, not product green —
k/12 remains the only green. After that note, Factory polls the
cerebrum-builds commit status context `store-gate` (optional
`store_gate.json` artifact) for the session's `build/**` SHA until
**12/12** `ok=true`, then stamps STORE green + `pilot_ready` so Floor
package ship can unlock. Continue after `HANDOFF_TO_N3` is **ingest**,
not another WRITER / Background Agent. Missing, red, or score ≠ 12/12
fail-closed.

### Floor recovery — `sess_02af51453b364e3f`

Live finance-ops session already handed off on Factory tip `edc03cc`
(receipt+diff clean; Floor painted failed / package 409). Cerebrum-builds
branch `build/sess_02af51453b364e3f-278c481a` tip
`081a52874bb0141c5eb730b01f26dcc0bf8d0fa2` has GHA run **34706583003**
`store-gate` success, description `acceptance.py in Docker 12/12`,
artifact `store_gate.json` score 12/12.

After this ingest ships:

1. **Auto-poll** — new sessions: the build thread calls
   `wait_and_ingest_n3` immediately after cli-pivot `HANDOFF_TO_N3`.
2. **Continue-as-ingest** — for `sess_02af51453b364e3f`, Floor Continue
   (or chat `start_coder`) must **not** open a fresh workspace. It
   resolves the builds SHA (ledger fields, or `build/<session>-*` on
   cerebrum-builds), fetches `store-gate`, and on 12/12 stamps
   `docs/store_acceptance.json` + `RUN_SUCCEEDED` `cycle=pilot`. Package
   unlocks when existing authorship / acceptance gates still hold.

Do not Continue into WRITER as a fake fix.

Do not Continue into WRITER as a fake fix.

### Persistence — Factory outputs on Render disk

`factory_outputs_root()` resolves to `$STORAGE_PATH/factory_outputs` when
`STORAGE_PATH` is set (Render: `/app/storage`). The production entrypoint
also `mkdir`s that directory and symlinks `/app/factory_outputs` → the disk
path so session-baked absolute paths and HANDOFF ledgers survive deploys.
After merge+deploy, Continue on HANDOFF sessions can ingest without WRITER.
If a ledger was already wiped before this fix, one-time HANDOFF reseed from
the known builds branch may still be required via
``POST /v1/sessions/{id}/product/n3-reseed`` (or
``POST .../product/generate`` with ``{"n3_reseed":true, "builds_sha",
"builds_branch", "cli_authored_ids"}``). That path stamps HANDOFF_TO_N3 then
calls ``ingest_n3_store_gate`` — it never launches WRITER / BA /
``generate_product``.


## N1a — live Cursor Background Agent

When `CURSOR_API_KEY` (or `CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY`)
**and** `CEREBRUM_BUILDS_GITHUB_TOKEN` are set, `launch_executor` cuts a
`build/<session>-<id>` branch from `cerebrum-builds` `main` (keeps
`.github/workflows/store-gate.yml`; override repo with `CEREBRUM_BUILDS_REPO`),
pushes the Factory workspace onto that branch, and launches a Cursor
Background Agent against it. The launch prompt is fixed; the model is
the Cursor account default (not hardcoded in Factory). After `FINISHED`,
Factory collects `receipt.json` plus the branch diff and hands them to N1b.
Compare HTTP 404 (missing seed/head after BA rename, branch delete, or ref
lag) is **not** treated as a GitHub outage: collect retries, rediscovers
`build/<session>-*` tips, falls back to the seed branch, then fails closed
as retryable infra with a seed/head diagnosis.

Keys, builds token/repo, Cursor API, never-started, hung-past-wall, and
push-failed misses stay `EXECUTOR_UNAVAILABLE`. Receipt/path misses stay N1b.

**The N3 store gate is still not green** until that workflow is live on
cerebrum-builds. A finished agent + clean receipt is only `HANDOFF_TO_N3`.

## CHADi 2026-09-12 — Option C Hybrid (two jails)

Cerebrum-builds / cli-pivot BA (`cli_receipt.ba_allowed_globs`) **may write
`tests/**`**. In-process Factory WRITER (`authority.py`) stays **sealed off
`tests/**` until N2**. Do not blanket-expand that jail. 12/12 remains cheat-resistance.
Still never expand either jail to `vendor/**`, `blocks.lock.json`, the ledger, or `.git`.

## Forbidden

**Do not run this gate on a Render worker.** Render web/worker services have no
Docker daemon. This workflow assumes GitHub Actions (or any host that already
runs `docker build`). A design that shells out to Docker from a Render start
command is rejected.

Not live on CerebrumDev.ai CI. This file stays under `docs/pivot/` until
cerebrum-builds exists.
