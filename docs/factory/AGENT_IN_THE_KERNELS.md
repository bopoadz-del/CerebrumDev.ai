# Where the agent sits in the build kernels — and where it could go next

Written after the first live manufacturing campaign against the real
Cerebrum-Blocks store (2026-08-13, builds 1–14 of `field_ops.yaml`). Records
the division-of-labour decision as built, the measured reasons for it, and the
two expansions worth doing — so the next session extends this deliberately
instead of re-deriving or accidentally reversing it.

## The decision as built

Five roles, one agent. COLLECTOR, CLONER, TESTER and STORE_MANAGER are
deterministic kernels; the coding agent lives only inside the WRITER, called
once per artifact (model spec, handler, route, README), inside lanes and gates
it cannot touch (`build/authority.py`, `build/gates.py`).

Why not "one agent doing every role, constrained by kernels":

- **The mechanical roles have exact answers.** Vendoring six blocks plus the
  runtime slice takes ~2 s and is byte-identical every run. Provenance
  (commit pins, lockfile, ledger) must be exact or the Store Manager's
  staleness question becomes unanswerable. An agent adds latency, token cost
  and new failure modes to work that needs no judgment.
- **The inspector must not be the builder.** Measured repeatedly in this
  campaign: the agent's characteristic failure is code that *looks* right —
  a handler reporting ok around a failed block call, a route returning None.
  Every fake green was caught by deterministic test machinery the agent
  cannot edit (its lane excludes `tests/`; the TESTER's lane excludes
  `app/`). An agent that writes the tests judging its own code will
  eventually weaken the test — that is the cheapest path to green.
- **"Defined jobs" as executable code beats defined jobs as prompt text.**
  A prompt instruction is advice; `assert_write_allowed` is a wall.

## What made the single-agent WRITER converge (the campaign's yield)

Each of these exists because a live build failed without it. They are the
convergence stack, in `build/roles.py` and `factory/coder.py`:

1. Findings name what failed (dispatch error envelopes carry block+action;
   generated tests collect every failing capability, not just the first).
2. Rework is a **ratchet** — only implicated capabilities regenerate; green
   specs/handlers/routes are reused verbatim.
3. Rework is an **edit** — the coder sees its own previous body next to the
   findings. Blind regeneration converged to identical wrong code five
   rounds running.
4. A statically rejected body earns one repair retry carrying the gate's
   reason.
5. The block contract carries everything discoverable at build time:
   block.json actions and defaults, `input_schema` required fields, and the
   requirement strings blocks answer in their own error literals.
6. Offline is enforced, not asserted: the generated conftest blocks outbound
   sockets, and the nested-results scan fails any green wrapping a failed
   block call.

## Future work: more agent, inside fixed kernels

Two places where agent judgment would add real value, both safe because the
kernel's own checks keep running and the agent can only add scrutiny, never
remove it:

### 1. A judging COLLECTOR (semantic block choice)
Today the COLLECTOR trusts the blueprint's `block_ids` verbatim. The
campaign showed the cost: `field_ops` binds site inspections to the
`validation` block, which is a Store-certification pipeline — a strained fit
an experienced engineer would flag at tender review. Extension: the agent
reviews capability↔block bindings against the harvested block contracts and
either endorses each binding or reports a mismatch as a named gap **before**
any build spend. Lane: report-only (the COLLECTOR stays read-only); the
human or blueprint author decides. No gate weakens.

### 2. An augmenting TESTER (agent-written domain cases)
Today the TESTER's suite is fully templated from the spec. It proves shape,
persistence and genuine block execution — it cannot know that a defect
raised as `severity=critical` should reject `close-out` without a
`resolution_date`. Extension: the agent proposes *additional* domain test
cases from the capability description; the kernel accepts them only if they
(a) pass on the current build, and (b) are mutations of spec-derived
payloads, never replacements of kernel tests. The kernel suite remains the
floor; agent cases can only raise it. The WRITER's lane still excludes
`tests/`, so the proposing agent call must run under the TESTER's authority,
not the WRITER's.

### Explicitly rejected (and why)
- Agent-driven CLONER/collector file operations: no judgment content, exact
  answers required, provenance must be deterministic.
- Agent-authored replacements for kernel tests: the inspector-is-not-the-
  builder invariant is the reason this factory's greens are believable.
- Letting the WRITER see or edit `tests/` "to understand failures": findings
  already carry the full failing assertion; the lane exclusion is what makes
  the rework loop honest.
