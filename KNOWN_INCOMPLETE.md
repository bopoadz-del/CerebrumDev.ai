# KNOWN_INCOMPLETE — CerebrumDev.ai

Honest register of functions `scripts/audit_stubs.py` flags as hollow in the
shipping `backend/app/` tree. Every entry below is either a `Protocol`
interface declaration (structural typing — a `...` body is correct; the real
implementation lives in the same module) or a benign, guarded fallback. There
are **no** unimplemented functions on a user/demo path.

Format: `- <path> :: <name>  — <reason>`

## Protocol interface declarations (`...` is correct; impl is real)
The estate dual-RAG layer IS implemented — `RagIndexStore` (JSONL persistence)
and `HashEmbedder` / FastEmbed provide the real bodies. These entries are the
`Protocol` method signatures used for structural typing.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: read_manifest  — RagIndexStoreProtocol declaration; impl at RagIndexStore.read_manifest.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: read_records  — RagIndexStoreProtocol declaration; impl at RagIndexStore.read_records.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: write_index  — RagIndexStoreProtocol declaration; impl at RagIndexStore.write_index.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: upsert_document  — RagIndexStoreProtocol declaration; impl at RagIndexStore.upsert_document.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: stats  — RagIndexStoreProtocol declaration; impl at RagIndexStore.stats.
- backend/app/factory/kits/private_estate_operations/rag/embeddings.py :: embed  — Embedder Protocol declaration; impl at HashEmbedder.embed (+ FastEmbed provider).
- backend/app/factory/build/gates.py :: __call__  — Gate Protocol declaration (structural typing for a phase check); the real bodies are the module-level gate functions gate_gaps_enumerated / gate_blocks_import_offline / gate_workspace_compiles / gate_suite_green / gate_store_ops_authorised, wired in the GATES mapping.

## Benign guarded fallback
- backend/app/resident_engineer/router.py :: _resolve_principal  — the `else` (non-estate, no steward-auth module) branch returns None; every state-changing resident route fails closed (401) when the principal is None, so a None here authorizes nothing.

## Deliberate no-op by policy
- backend/alembic/versions/0001_baseline.py :: downgrade  — the baseline migration's reverse would be `DROP TABLE` on every accounts table; destroying user/account data is never an acceptable automated rollback. Schema rollback below the baseline is a manual, operator-decided act (restore from backup).

---

## Open work — registered, not fixed

Not hollow functions. These are known-incomplete *systems*, listed so nobody
reads a green suite as "finished". `scripts/audit_stubs.py` ignores this
section (it only parses `- ` lines containing `::`).

### 1. Role runner — BUILT; cutover and the LLM writer are NOT
`app/factory/build/runner.py` binds the three kernels and drives a real
build: phase order via `assert_phase_order`, a lane-restricted `RoleWorkspace`
per role, the phase's gate after each role, the WRITER↔TESTER rework loop, a
budget whose exhaustion is a recorded failure, and resume from the ledger.
It runs end to end and produces a platform that invokes vendored blocks
locally instead of calling the store over HTTP.

Three things are genuinely still open:

**(a) Cutover has not happened — this is the next milestone.** The runner is
opt-in behind `FACTORY_RUNNER_ENABLED` and nothing in the HTTP or chat
generation path calls it. `ProductGenerator` remains the default and still
emits `httpx.post(store_url + "/v1/execute")` handlers. Both paths exist side
by side on purpose; retiring the template path is a separate piece of work.

**(b) The WRITER does not call the LLM coder yet.** It composes handler
bodies from the block contract deterministically. The seam is a single
function (`roles._templated_body`) and the module records which path produced
each handler, so nothing claims LLM authorship that did not have it. This is
the difference between "the runner manufactures" and "the agent manufactures"
— the harness is real, the coder is not yet plugged into it.

**(c) STORE_MANAGER is minimal — DECIDED: deferred, not implemented.** It
records the clone manifest and executes no `StoreOp`. `store_manager.py`
defines 15 ops with approval gates and every consumer still only prints the
manifest. Deferred deliberately rather than half-built: executing store ops
means writing to the Cerebrum-Blocks repo (publish, version, deprecate) with
a human-approval path for MAJOR and DELETE, plus the registrar queries over
`iter_ledgers()` for staleness. That is its own milestone, and a partial
implementation that writes to the Store without the approval path is worse
than none.

The factory rebuild is **in progress, not concluded**.

### 1b. Cutover is the next milestone
The runner is opt-in (`FACTORY_RUNNER_ENABLED`, or the `cli.py build`
subcommand) and nothing in the HTTP or chat generation path calls it.
`ProductGenerator` remains the default and still emits
`httpx.post(store_url + "/v1/execute")` handlers. The runner's artifact is now
~23 files (models, sqlite persistence, FastAPI routes, entrypoint, README,
requirements, a real test suite) against `ProductGenerator`'s ~93, so parity
is closer but not reached — the runner does not yet emit hats, workflows, the
universal console, connectors, edge profile, certification scaffold, Product
DNA or the Resident Engineer. Decide cutover on what the live test shows.

### 1c. Agent output quality is model-bound — MEASURED, and the first reading was wrong
`kimi-k2.7-code` builds the smoke blueprint end to end: **SUCCESS, rework 0,
7 of 10 artifacts agent-written, no coder failures**, passing the strict route
test first time. The agent path works.

`moonshot-v1-8k` does not — it writes a route returning `None` and cannot fix
it across three rework rounds, ending `FAILED_BUDGET_SPENT`. The runner
behaved correctly in refusing to ship it.

**The original conclusion here — "K2-class models are required but unavailable"
— was wrong, and the cause is worth remembering.** `coder.py` hardcoded
`temperature: 0.2`; every kimi-k2.x/k3 model answers `400 invalid temperature:
only 1 is allowed for this model`. The coder caught the HTTPStatusError,
recorded a CoderError and shipped templates, so builds reported SUCCESS with
`agent_written: 0` and the symptom read as a weak or unavailable model. The
request never reached the model. Fixed: the temperature is now sent only when
`LLM_TEMPERATURE` is configured, matching what `llm_config._llm_temperature()`
already documented. Regression-tested in `tests/test_coder_temperature.py`.

Also fixed: the factory fallback default was `kimi-k2.5-code`, which returns
`404 Not found the model` — the retry leg had never been real.

### 2. CEREBRUM_LLM_API_KEY missing on the Render backend — BLOCKS PRODUCTION
**Owner: Chadi. Dashboard secret. Not fixable in code — do not work around it.**

The Render service `cerebrumdev-backend` (`srv-d9ta2pad0e5s738lllpg`) has no
LLM key set. Factory drafting there falls back to keyword mode and the coder
cannot run at all, so a production factory run cannot produce any
agent-written capability — every artifact would silently take the template
path. Local development is unaffected (`backend/.env` carries a key).

Note `/ready`'s `llm_configured` field reports `true` regardless
(`app/main.py`, `or os.getenv("LLM_PROVIDER")`) — do not trust it as evidence
the LLM works. Verify instead with the `coder_failures` field of a build, or
`POST /v1/factory/product/draft` and check `drafting_mode`.

### 3. Steward blocks dual-register against the factory's own mirror
`estate_registry` and `portfolio_rollup` do not exist in the real
Cerebrum-Blocks repo (109 blocks, neither present).
`dual_registry.load_blocks_registry` merges `app/factory/vendor_blocks_mirror`
into the registry unconditionally, so those two satisfy the dual-registration
gate against a copy the factory ships to itself. Pointing `blocks_root` at a
real Store checkout changes nothing. Pinned by
`test_steward_blocks_come_from_the_mirror_not_the_store`, which is designed to
go red when they land upstream — that is the cue to drop them from the mirror.

### 4. Generation is not byte-reproducible when the coder runs — RESOLVED for resume
Same blueprint, two builds, different tree hash — because an LLM does not emit
the same bytes twice. This is irreducible, not a bug to fix.

It no longer blocks resume. `ProductGenerator`'s `inputs_hash` is misnamed: it
is `hash_tree` of the *generated output* (`generator.py`), so it moves with any
LLM-written file and could never have been a resume key anyway — the value is
unknown until the build it is meant to authorise has already run. The runner
uses `runner.blueprint_hash()` instead: canonical sorted-key JSON of the
blueprint, hashing the build's actual inputs. Stable by construction, proven
by `test_blueprint_hash_is_the_resume_key_and_is_stable`.

Standing constraint: never add a provenance or resume check that hashes the
generated tree. The scaffold's own reproducibility is asserted separately (see
"Running the tests" in README.md).
