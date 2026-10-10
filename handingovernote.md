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

---

# Update — cloud agent, 2026-10-08 evening

## Landed (each: CI green → replay incl. known-bad → squash merge → one deploy → health)
| Repo | PR | What | sha | runs |
|---|---|---|---|---|
| cerebrum-builds | #42 | Known-bad replay fixture as data (`.github/store_gate/known_bad_fixtures.json`, branch `known-bad/image-omits-runtime-dir` @ `60d7cd08`, expected `cross_tenant_404`); replay = every certified still certifies AND every known-bad fails on its expected check | `e2943ec` | #40 gate certified it: 37777375246; today's gate fails it: 37777370845; replay 37779747670 |
| CerebrumDev.ai | #712 | Batch: #707 cycle-2 fixes, #709 rotation v2 (repeat last failure, then fresh; certified never re-run; seed is data), #710 writer never authors Factory-owned files (restore + rework naming the path), item 4A emulated image self-sufficiency on every writer pass (`image missing <path>`), 7b smoke asserts PID 1 survives `kill -TERM 0` from an agent shell, clean-runner fixes for the 3 pre-existing red suites | `5410237b` | replay 37802475891; deploy 37805788273 |
| CerebrumDev.ai | #713 | Loader: `verify_locked_blocks()` refuses to start if a locked block can't load; image check refuses a block loaded outside its locked path (`locked_block_loaded_elsewhere` — names 7 `app/local_blocks` fallbacks in the revoked smoke B). Hardwiring gate blocks every commit (`.githooks/pre-commit`, install with `scripts/install_git_hooks.sh`). Cycle-3 fix: build refresh renders absent Factory-owned files (`render_absent=True`) | `55d91d62` | replay 37815196594; deploy 37827522232 |

**Live Factory: `55d91d62`** (cycle 4 started on it, `/version` check green). #706/#707/#709/#710 closed (landed via #712).

## 7a — #701 red run
https://github.com/bopoadz-del/CerebrumDev.ai/actions/runs/37802479551/job/113398005899 — test on pre-#701 `0e50fcc`: 4 failed / 1 passed (`await_cli` ≠ `continue_ceiling`). pull_request CI can't show this (it tests the merge with master); a proof-only push workflow on `agent-h3/red-701-proof` ran it at the exact commit. #711 closed.

## Cycle results
| Cycle | Commit | Smoke A | Co-op | Vineyard | Pick 1 (repeat) | Pick 2 (fresh) | Smoke B |
|---|---|---|---|---|---|---|---|
| 3 (run 37806645155) | 5410237b | fail | fail | fail 20/22 (gate 37811318058) | fintech: failed, no WRITER commit (43 min) | motor insurance: failed, no WRITER commit (42 min) | fail |
| 4 (run 37828266456) | 55d91d62 | **pass** (gate 37834740186) | **certified** 422,850 B (gate 37833911168) | **certified** 472,113 B (gate 37832735476) | fintech: failed, no WRITER commit (51 min) | fleet delivery logistics: failed (30 min) | fail — build `plt_4b053125e56d4580` never reached the gate (no store-gate run); cause unread |

- Cycle 3 smoke A/co-op/vineyard/smoke B = **Factory** regression from #710: the guard removed the writer's ci.yml and the refresh rendered ci.yml only "if present" → base branch's pytest-only workflow shipped → `ci_present_and_full_suite` + `audit_clean` FAIL. Fixed in #713; cycle 4 builds carry the Factory ci.yml and pass the gate.
- **Rotation picks (fintech ×2, motor, fleet) + cycle-4 smoke B: UNCLASSIFIED — next task.** Note rotation v2 repeated only fintech in cycle 4; motor (failed in cycle 3) was not repeated — check that is the intended rule. Both reach CLONER, then the WRITER never commits; the cycle report reads `failed` at ~42–53 min (≈ the 2700 s writer wall). Anchors in the same cycle pass. The reason is in the live build ledger / build-status, which the cloud sandbox cannot reach (api.cerebrum-dev.com and the Actions artifact store are proxy-blocked). Read `build-status` + ledger for the sessions of `build/plt_2a3278c2274c4ac9`, `plt_7ba648b1bdd341da` (cycle 3), `plt_ae9e0e55f12444e6`, `plt_e130ee02014843a4` (cycle 4 fintech/fleet), `plt_4b053125e56d4580` (cycle 4 smoke B) before touching anything. Both are rotation picks with typed intake (country/currency, money contract) — check the writer receipt and the money/image gates first.

## Still to do
1. Classify + fix fintech/motor writer failure (above). Rotation v2 will repeat fintech next cycle.
2. **Smoke B re-cert** (`plt_5ac16f50…`, session `sess_ad4e3cbb755543a5`, account 0) through the Factory rework path — NOT done: needs the live API, unreachable from the cloud sandbox. Certifies only if the image check passes on every locked block.
3. **Item 4B** (in-image gate step in store-gate.yml) — separate gate PR, after a green cycle.
4. **7c** fintech export client-share stripping + Your Platforms download — once fintech certifies; Your Platforms needs a UI check.
5. Fintech `provenance.json` root cause (from the note above): the CodeWhale writer invented it from blueprint block_ids / blocks.lock.json because `docs/provenance/**` was a writer lane and fill-gaps converge kept it; #710 now makes it Factory-owned (restored + rework).

## New rules / tools since the note above
- Run `scripts/install_git_hooks.sh` in every clone: the hardwiring scan blocks the commit. Never pipe the scan (`scan | tail` masks its exit code — how a new form was once pushed).
- Every gate PR replays incl. the known-bad fixture; a replay passes only if the known-bad build fails on its expected check.

---

# Update — cloud agent, 2026-10-09 → 10 (ops on Actions, cycles 5–7)

## The proxy is not a wall: ops.yml
Everything that needs `api.cerebrum-dev.com` or AWS runs on Actions: **`.github/workflows/ops.yml`** (`scripts/ops.py`). Dispatch with `gh workflow run ops.yml --ref master -f action=… [-f target=…] [-f run_id=…] [-f branch=…]`, **one target per dispatch** (concurrency is per target). Answers go to the run summary AND are committed to branch **`ops-results`** under `runs/<run id>/` — read them with `git fetch origin ops-results; git show origin/ops-results:runs/<id>/<file>`.
- `ledger-dump` — full build ledger + service log lines for a session (target = session id, platform id, builds branch, or a cycle run name with `run_id`).
- `gate-dispatch` — Store gate on a builds branch; with a target, then Continue on that session and follow it to its end.
- `export-check` — export zip via the API, strip audit, Your Platforms listing.
- `service-events` — ECS events, stopped tasks + exit codes, memory/CPU maxima, lifecycle log lines. AWS only; never calls the API.
Every job fails closed with one readable reason. The live API endpoint `GET /v1/sessions/{id}/product/ledger` (owner-scoped, sanitized) serves the full ledger.

## Landed
| Repo | PR | What | sha |
|---|---|---|---|
| CerebrumDev.ai | #714 | ops.yml + ledger endpoint; distinct writer failures are distinct (`_failure_keys` uses declared `finding_shape`, else the gate's reason); gate-run probe env; withdrawn certification reopens the Store gate; cycle 3 voided in the rotation seed | `d024b231` |
| CerebrumDev.ai | #715 | `factory_owned.prestamp` renders every renderable owned file before each writer pass; same failure = same files touched the same way (`finding_shape`) | `75a5a1ef` |
| cerebrum-builds | #43 | Store gate off Docker Hub's anonymous limit: postgres service from `public.ecr.aws/docker/library/postgres:16`; product image built through a BuildKit builder mirroring docker.io via `mirror.gcr.io` (falls back to plain build); runner disk freed first. Replay: 3/3 certified 22/22, known-bad still caught on `cross_tenant_404` | `847e50b` |
| CerebrumDev.ai | #716 | **Exit-signal guard**: uvicorn (PID 1) refuses SIGTERM/SIGINT while a coding agent is live or ended <30 s ago (`agent_process.guard_exit_signals`, installed in the lifespan); smoke reads DEAD without it. ops `service-events`; ops-results commit retries ×8; `gate-replay.yml` has `workflow_dispatch` (input `sha`); production image base from `public.ecr.aws/docker/library/python:3.11-slim-bookworm`; cycle 6 voided | **`a3e1fd7`** (live) |

## Cycle results
| Cycle | Commit | Smoke A | Co-op | Vineyard | Pick 1 | Pick 2 | Smoke B |
|---|---|---|---|---|---|---|---|
| 5 (37952813841) | d024b231 | pass | fail | fail | fintech fail | **motor insurance CERTIFIED** 523,358 B; export-check PASS (ops 37968033430) | fail |
| 6 (37972176731) | 75a5a1ef | pass | orphaned | orphaned | fintech orphaned | clinic orphaned | pass — **cycle voided** |
| 7 (37997314010) | a3e1fd7 | **fail** | **CERTIFIED** | fail | fintech fail | **clinic CERTIFIED** (fresh) | running at handover |

- Cycle 5 failures: `writer_authored_factory_file` twice — owned files absent at writer start (fixed #715).
- Cycle 6: at 18:57:52Z the server logged `Shutting down` in the same second fintech's writer CLI exited; ECS stopped nothing, memory 33% (ops service-events 37985812586). Agents already sit in their own session, so the signal was aimed at the server by name/pid (products serve `app.main:app` too — `pkill -f` on a probe server matches). Every writer was orphaned. Fixed by the exit-signal guard (#716). The inferred `pkill` is not yet proven from a transcript (the writer's `docs/writer_progress.*` is on the service's disk; nothing serves it).
- Cycle 7: guard proven live — smoke A: `server refuses exit signals while an agent runs — samples=68 guarded=68`, no restart since the 22:03Z deploy (ops 38002880238). Ledgers (ops 38005613672 / 38005618405 / 38005622872 / 38005627283):
  - fintech `sess_ff83576d9ec541f3`: reworks UI wiring → TESTER collection → writer created `conftest.py` → writer created `docs/provenance/provenance.json` → **STOP** (WRITER gate budget).
  - vineyard `sess_5502380a293a493d`: TESTER `cross_tenant_404` (401≠404) → created `conftest.py` → UI wiring → created `conftest.py` again → **STOP**.
  - smoke A: STOP on `provenance.json` created by the writer.
  - **Classification: Factory** — #715's prestamp skipped the two owned files rendered only after the pass (TESTER's rootdir bootstrap, converge's provenance); writers create them for their own self-check.

## Open at handover
1. **Branch `fix/prestamp-bootstrap-and-provenance`** (commit "TESTER's bootstrap and the provenance record exist before every writer pass"): `factory_owned.prestamp_late_files` renders `conftest.py` (fixed template `_CONFTEST`) and the provenance record (`converge.provenance_record(ctx)`, now shared with converge) when absent; the writer step passes `ctx`. Targeted tests green (34). **Next:** open the PR → CI → Gate replay (it touches no gate-path file, so dispatch it: `gh workflow run gate-replay.yml --ref fix/prestamp-bootstrap-and-provenance -f sha=<head>`) → wait for cycle 7 to finish → squash merge → deploy → cycle 8 (rotation: fintech repeat; vineyard is an anchor).
2. **Smoke B re-cert** of `plt_5ac16f50c9384536` (session `sess_ad4e3cbb755543a5`, account 0) — not done; run after a cycle: `gh workflow run ops.yml --ref master -f action=gate-dispatch -f branch=build/plt_5ac16f50c9384536 -f target=sess_ad4e3cbb755543a5`. Expect 21/22 → writer rework → export; report the rework diff.
3. **Fintech export-check** once fintech certifies (`action=export-check -f target=<fintech sess>`).
4. Small follow-up: add `"exit-signal guard"` to `LIFECYCLE_TERMS` in `scripts/ops.py` so `service-events` shows refusals.
5. **Fork RAG_SCRUB_RULES** — draft **the_fork#863** (`fix/scrub-rules-as-data`, head `fe6c379`), DO NOT MERGE: coverage run 37945248586 — 11 secret rules, 9 match the live corpus, 10,781 matches, structural rules cover 0. Owner decision needed; committing the names would itself leak them.
6. Owner UI checks: Fork #852/#854, Your Platforms download.

## Notes
- Docker Hub throttled/failed the runners 2026-10-09 ~20:59–21:35Z (429, then auth 504). Gate and production image now pull from mirrors; do not reintroduce bare `FROM python:…` in the Factory Dockerfile (`backend/tests/test_production_image_base_registry.py`).
- `gh run download` / job-log blob URLs are proxy-blocked in the sandbox; read logs via the GitHub MCP `get_job_logs` (`return_content=true`) or route through ops.yml.
- The pre-commit hardwiring scan takes ~3 min; run commits in the background and never pipe the scan.
