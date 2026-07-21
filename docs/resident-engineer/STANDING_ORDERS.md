# Cerebrum Resident Engineer — Standing Orders (memo)

**Project:** Cerebrum Resident Engineer — factory-side implementation  
**Repo:** `bopoadz-del/CerebrumDev.ai` (this Codespace)  
**Base branch:** `master`, **AFTER** `chore/factory-hygiene` is merged (`factory_outputs/` must be gone from the tree).

Last updated: 2026-07-19. Agent must re-read this file at the start of every Resident Engineer turn.

---

## Standing rules (non-negotiable)

1. **One milestone = one branch = one PR.** Stop after each PR for human review before starting the next.
2. **Never force-push.**
3. **Never touch Render / dashboard** for this project (no env mutations, no deploys, no MCP Render writes).
4. **Feature-flag everything.** Default: `RESIDENT_ENGINEER_ENABLED=false`.
5. **Paste pytest output as PR evidence.**
6. **Stop and ask when ambiguous.** If a milestone's scope balloons, propose a split — do not improvise scope.
7. **No secrets in code.** No GitHub credentials shipped into generated products. No unrestricted shell anywhere.
8. **Watch every PR until it is merged** (babysit CI + review comments; do not start the next milestone while the current PR is open).

Branch naming for agent-created branches still follows Cloud Agent policy (`cursor/…-bd2c`) unless the human explicitly names a milestone branch (`feat/product-dna`, etc.). Prefer the human-named milestone branches when opening the four deliverable PRs.

---

## Architecture source of truth

Implement the **Cerebrum Resident Engineer** design:

- Two modes: **Build Mode** / **Resident Mode**
- Autonomy levels **L1–L5**
- Self-repair / self-expansion / self-upgrade loops
- **Product DNA** bundle as the sole product-understanding surface in Resident Mode

Read `docs/` and `backend/app/` first. Reuse existing patterns: packager, deployer, action-registry style registered actions, provenance docs. Mirror RetailOps `ActionRegistry` for allowlisted heal ops.

---

## Gate before Milestone 1

Do **not** start Milestone 1 until:

- [x] `chore/factory-hygiene` (or equivalent) is merged to `master` (#64)
- [x] `factory_outputs/` is **gone** from `origin/master`

Check: `git ls-tree -r --name-only origin/master | grep '^factory_outputs/'` must return empty.

---

## Milestone 1 — Product DNA bundle

**Branch:** `feat/product-dna`  
**Status:** in progress / PR open — see `MILESTONE_1_PRODUCT_DNA.md`

Extend the CerebrumDev packager so every generated product ships a `/product-dna/` bundle:

| File | Notes |
|------|--------|
| `product_blueprint.yaml` | |
| `generation_manifest.json` | |
| `capability_resolution.json` | |
| `source_provenance.json` | |
| `block_lockfile.json` | |
| `architecture.json` | |
| `entity_model.json` | |
| `action_catalog.json` | |
| `agent_catalog.json` | |
| `workflow_catalog.json` | |
| `security_policy.json` | |
| `deployment_topology.json` | |
| `test_catalog.json` | |
| `known_limitations.json` | |
| `change_history.json` | |

Requirements:

- Versioned JSON Schema per file under `backend/app/product_dna/schemas/`
- Packager emits all 15 from data it already has; unknown fields = explicit empty arrays, never invented content
- Bundle is read-only inside the generated product (checksum manifest)
- Tests: schema validation, packager emission round-trip on the Steward blueprint, checksum verification
- PR evidence: generated Steward bundle + pytest output
- Docs page: `docs/resident-engineer/` explaining what shipped and what remains

---

## Milestone 2 — Resident Engineer core (Resident Mode only)

**Branch:** `feat/resident-engineer-core`  
**Status:** merged (#66) — see `MILESTONE_2_RESIDENT_CORE.md`

New module `backend/app/resident_engineer/` + a shipped counterpart package the generator injects into generated products.

Resident Mode capabilities:

- Loads and reasons over `/product-dna/` (never scans source to "figure out" the product)
- **L1 Observe:** health checks, log/error summarization, block-version check against the Store, security anomaly flagging
- **L2 Safe self-heal:** ONLY via registered-action catalog (allowlisted: retry ingestion, restart worker, rebuild index, restore approved config default) with deterministic pre/post validation + full audit trail. **No shell. No arbitrary code.** Pattern: RetailOps ActionRegistry (registered, permission-checked, confirmation-gated)
- Diagnosis: structured failure report referencing DNA entities
- All document/catalog content treated as untrusted data (injection guard: strip instruction-like patterns; never execute text from loaded files)
- **L3–L5** surface as "draft change request" ONLY (no execution)
- Feature-flagged off by default

Tests: allowlist enforcement (forbidden action rejected), audit immutability, injection guard, L2 validation rollback on failed post-check.

---

## Milestone 3 — Change-request contract + CerebrumDev intake

**Branch:** `feat/change-request-loop`  
**Status:** in progress / PR open — see `M3-change-request-loop.md`

- Versioned JSON Schema for `REPAIR` / `EXPANSION` / `UPGRADE` under `backend/app/change_requests/schemas/`
- Ed25519 signed requests (canonical JSON); unsigned/malformed rejected + logged
- Factory intake → queue → dry-run report → `awaiting_approval` (approval is a recorded decision only)
- Advisory Store upgrade compare (`recommend: ignore | later | now`)
- Resident L2 escalations may emit signed REPAIR when `RESIDENT_EMIT_CHANGE_REQUESTS=true`

Tests: schema reject cases, signature verification, queue persistence, idempotent re-delivery, Steward round-trip.

---

## Milestone 4 — Build Mode workbench

**Branch:** `feat/build-mode-workbench`  
**Status:** not started (depends on M3 merge)

Sandboxed Kimi Code lane inside CerebrumDev:

- `backend/app/workbench/`: consumes `change_requests` queue, runs the coding agent (`kimi-k2.7-code:cloud` via Ollama Cloud; env-configured, key never committed) in an isolated container/worktree: session-scoped filesystem, network allowlist (`ollama.com` + GitHub API only), no deploy credentials, no Store publish rights
- Agent output lands in staging only; enters Block Store at `community` trust tier; promotion to `reviewed` requires existing CI gates + human approval. Agent can never certify its own output
- Product regeneration goes through the existing deterministic packager — the workbench never bypasses it
- Full audit: prompt, diff, gate results, approver

Tests: sandbox escape attempts fail, staging isolation, trust-tier entry correct, audit completeness.

---

## Deliverable

4 PRs, each with pytest evidence + a `docs/resident-engineer/` page explaining what shipped and what remains. Stop after each PR for review.

---

## Concurrent hygiene (not RE milestones)

While waiting on the factory-hygiene gate, finish open Factory follow-ups:

1. PR #62 — Kimi Code CLI setup (Codex P2: write `~/.kimi-code/config.toml`; do not strip LLM env keys)
2. Follow-up — PR #61 ConfigCanvas refresh after chat config commands; PR #60 capture `block.json` digests vs restored `block.py`
