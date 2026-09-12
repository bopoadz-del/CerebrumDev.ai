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

## CHADi 2026-09-12 — Option A (BA path jail)

BA/WRITER enforce (`cli_receipt.writer_allowed_globs`) **may write `tests/**`**.
12/12 remains cheat-resistance. Still never expand to `vendor/**`,
`blocks.lock.json`, the ledger, or `.git`.

## Forbidden

**Do not run this gate on a Render worker.** Render web/worker services have no
Docker daemon. This workflow assumes GitHub Actions (or any host that already
runs `docker build`). A design that shells out to Docker from a Render start
command is rejected.

Not live on CerebrumDev.ai CI. This file stays under `docs/pivot/` until
cerebrum-builds exists.
