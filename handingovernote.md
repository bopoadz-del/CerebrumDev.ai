# Handover note — K-EXPORT / rotation release bar (2026-10-08)

Written by the outgoing Claude Code session (laptop) for the cloud agent taking over.
Repos: Factory `bopoadz-del/CerebrumDev.ai`, gate `bopoadz-del/cerebrum-builds`, Store `bopoadz-del/Cerebrum-Blocks`.

## Binding owner rules (do not relax)
- **NO HARDWIRING.** Nothing keyed on blueprint, vertical, project, session, branch, document name or sha. Lists are data files; the hardwiring gate (`scripts/scan_hardwiring.py`, incl. form `blueprint_name`) stays at 0 with an empty baseline.
- Merge rule: PR → CI + certified-build replay green → merge → **one deploy** (deploy-aws.yml, never the laptop) → health → cycle. **Every push to master deploys and auto-starts a release cycle** — batch PRs into one branch (merge other branches into it, no force-push) so a batch is one deploy.
- **Gate PRs (cerebrum-builds gate paths or Factory gate modules) may not merge without the known-bad replay fixture (item 2 below) passing.**
- Never deploy, merge a Factory PR, or change AWS while a cycle runs. Never loosen a check (incl. `cross_tenant_404`). Never patch a product branch by hand; product defects go back to the writer as rework.
- Classify every failure first: Factory regression / gate defect / test defect / product (writer rework). A gate failing a previously certified export is a gate defect until proven otherwise.
- Don't stop to ask; restarting a watcher never needs owner approval. Account 0 is reserved for the smoke.
- Commit identity `CHADi mahmoud <shadido.dxb@gmail.com>`. Tests first (failing on master), one pytest at a time on small machines.

## Live state
- Live Factory: **`94d40722`** (#705), backend task def **`cerebrumdev-backend:5`** (2 vCPU / 4 GB, `FACTORY_WORKER_PROFILE=2c-4g`; size is declared in `infra/backend_task.json`, applied by deploy-aws.yml).
- cerebrum-builds main: **`92f3b72c`** (#41 merged: an older harness always judges the image; certification revocation as data in `.github/store_gate/revoked_certifications.json`). Note: #41 merged minutes before the known-bad rule was given, so it has not yet been replayed against a known-bad fixture — do that first (item 2).
- Release cycle runs on GitHub Actions (`post-deploy-smoke.yml`, `scripts/release_cycle.py`): resolve live sha → smoke A ∥ repros (anchors co-op + vineyard, 2 rotation picks from `backend/tests/repro_pool/pool.json`) → smoke B + report artifact `release-cycle-report`.

## Cycle results so far
| Cycle | Commit | Smoke A | Co-op | Vineyard | Pick 1 | Pick 2 | Smoke B |
|---|---|---|---|---|---|---|---|
| 1 (run 37732081706) | 98d3a356 | fail (fixed-wait, fixed #703) | fail (TESTER overwrote product conftest, fixed #705) | **certified** 482,492 B | fintech: crash (copy onto itself, fixed #705) | hotel ops **certified** 630,961 B | pass |
| 2 (run 37755589836) | 94d40722 | fail (harness not in minimal image, fixed builds#40/#41) | fail (checkpoint carried only store-gate.yml, fixed in #707) | **certified** 431,820 B (gate run 37758037593, in-image, valid) | fintech: `provenance_complete` unknown commits (fixed in #707) | hotel ops **certified** 552,608 B (gate run 37761321566, valid) | pass |

Revoked: store-gate run 37767964892 on `plt_5ac16f50c9384536` (cycle-2 smoke B build): false 22/22 — harness ran from the checkout under #40; the image has no `vendor/` (Dockerfile copies only app/alembic) and `app/dispatch.py:29-34` loads `/app/vendor/blocks`. Real score 21/22 (`cross_tenant_404`, BlockNotVendored) = **writer defect**.

## Open PR to land next
- **Factory #707** (`agent-h3/cycle2-fixes`, head `d76f1f6e`, NOT merged): Factory owns provenance commit keys (`converge.factory_provenance_text`); Store commit never falls back to the deploy sha (`generator.py` git_head env_fallback=False); checkpoint carries main's whole gate (`STORE_GATE_PATHS`, `branch_attach.carry_mains_gate`); harness reads `ACCEPTANCE_RUNTIME_ROOT` (#706 folded in; close #706 when #707 merges); export refuses to present a revoked certification (`n3_store_gate.certification_withdrawn`). Its last replay (37768176412) flipped `plt_5ac16f50…` — that baseline is now revoked by builds#41, so **re-run #707's replay**. Gate PR → also needs the known-bad fixture first.

## Owner's TASK still to do (all unstarted in the repo — agents were stopped before pushing)
1. Merge #707 when CI + replay (incl. known-bad) green → one deploy → health → record sha.
2. **Known-bad replay fixture**: a generated build (Factory renderers; NOT plt_5ac16f50) whose Dockerfile omits a directory the app loads at runtime (vendor/ shape); listed as data (e.g. `.github/store_gate/known_bad_fixtures.json`: ref, expected failing check, reason). Replay passes only if every certified build still certifies AND every known-bad still fails on its expected check (failing for another reason ≠ caught). Prove the #40 gate (14ea4895) would pass it and the current gate fails it. Surface in the Factory gate-replay check too (`backend/app/factory/build/gate_replay.py`, `.github/workflows/gate-replay.yml`).
3. **Writer never authors Factory-owned files**: one declared list (reuse stamp_registry / authority lanes / STORE_GATE_PATHS / receipt Factory set): `docs/provenance/provenance.json`, stamps, gate files, handoff records, receipt, root `conftest.py`. Writer edits → rework naming the path, never silent overwrite. **Open question to answer with evidence**: why the cycle-2 fintech writer (`sess_59090c3bd0964425`, `build/plt_464389e32e544810`, commit `22e6be03`) authored its own `provenance.json` (keys product_id/schema_version/sources/bindings) — check prompt/templates/Store READMEs/examples reachable on the shared build volume; fix the cause.
4. **Image self-sufficiency check** in the writer's own build step (before the gate): after docker build, import the app and load a vendored block via the product's own loader inside the image; fail "image missing <path>". Generic, derived from the app's imports. Must catch the smoke-B shape first, gate second.
5. **Smoke B re-cert** (after #707 deploy): normal gate dispatch on `plt_5ac16f50…` → expect 21/22 → normal writer rework round (session `sess_ad4e3cbb755543a5`, account 0) → export. No manual Dockerfile patch.
6. **Rotation v2** (`scripts/repro_pool.py`, `scripts/release_cycle.py`): slot 1 repeats the previous cycle's failing pick; slot 2 = next unused pool blueprint; never re-run an already-certified one. State derived from cycle reports + a committed seed (hotel ops certified in cycles 1–2). **Cycle 3 = smoke A + co-op + vineyard + fintech (repeat) + motor insurance (fresh) + smoke B.** Today's selector (k from the last report: failed → same pair) would pick fintech + hotel ops — must change before cycle 3.
7. Carry-overs: (a) **#701 red run link** — run `backend/tests/factory/test_ceiling_bump_sees_writer_progress.py` (on master since #702) against `0e50fcc1` in CI (throwaway draft PR, then close) and record the failing job URL; locally it fails 4/5 on `assert 'await_cli' == 'continue_ceiling'`. (b) **Smoke assertion kill-0**: smoke-gated spawn through `agent_process.agent_popen_kwargs()` running `kill -TERM 0`, server must stay alive and serving (extend the #703 isolation endpoint + `scripts/post_deploy_smoke.py`). (c) **Fintech export** client-share stripping (same as the construction export) + Your Platforms download, verified on the deployed sha, once fintech certifies.
8. Cycle 3 on Actions on the deployed sha; classify any failure before touching anything.

## Report format the owner wants
One line per landed step (PR, sha, run link). Final: deployed sha; replay result incl. known-bad fixture; cycle-3 per-blueprint verdict table; #701 red run link; smoke-B re-cert score and rework outcome; the fintech provenance.json root cause.
