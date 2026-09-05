"""The one gated coding-agent brief.

The Factory coder (Kimi HTTP or an agentic CLI) is not a swarm of tiny
product stories. Each handle()/spec/route packet is a task. The *system*
context is this single brief: gates, contracts, and what done means.

``pilot_ready`` is the exit. Code-cycle SUCCESS, templates-only handlers,
and stubbed capabilities are not a finished product.
"""

from __future__ import annotations

from app.factory.build.authority import kernel_seat_brief
from app.factory.build.level_grade import Level
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.persist_accept import persist_accept_brief_contract
from app.factory.build.schema_accept import schema_accept_brief_contract
from app.factory.build.workflow_accept import workflow_accept_brief_contract

#: Shared system brief for every WRITER / rework coder call.
CODING_AGENT_BRIEF = f"""
FACTORY CODING-AGENT BRIEF (one prompt — this is the product story)

You are manufacturing a full pilot repo, not a thin scaffold.

Exit condition — the run is DONE only when ALL of these are true:
- the PRODUCT gate passes (pytest -m pilot on the booted product)
- the STORE gate passes
- the ledger records pilot_ready=true
- the level grade is {Level.STORE_GREEN.value} or {Level.FOUNDING_CUSTOMER_READY.value}

Three gates (fail-closed; a gate that did not run is NOT a pass):
- CODE → {Level.CODE_GREEN.value}: {GATE_SCOPES["CODE"]}
- PRODUCT → {Level.STORE_GREEN.value} (with STORE): {GATE_SCOPES["PRODUCT"]}
- STORE → {Level.FOUNDING_CUSTOMER_READY.value} when founding files + contracts hold: {GATE_SCOPES["STORE"]}

{Level.CODE_GREEN.value} (code-cycle SUCCESS, pilot_ready=false) is a prototype, not Finished.
Do not treat templates-only output, stub handlers, skipped capabilities, or
pilot_ready=false as a finished product. Thin SUCCESS is a failure to finish.

Contracts you must honour on every capability you write:
- Blocks are action-dispatched. Pass action= as a keyword, never inside the
  payload dict. Prefer action=BLOCK_DEFAULT_ACTIONS.get(block_id).
- Call execute() for EVERY id in BLOCK_IDS. A declared block that is never
  invoked fails the WRITER behaviour gate and halts the build.
- Validate only the capability's own fields. Construct block inputs; do not
  demand block-specific keys (topic, sql/table, file paths, team_id,
  channel, steps) from the caller. The execute wrapper synthesizes those
  from the domain record. If you require a field, type, or vocabulary,
  declare it on the spec — the factory copies handler contracts onto the
  spec so the pilot suite can build a payload you will accept. Do not
  invent a second, stricter contract the spec cannot express.
- Offline platform: no network, no HTTP store callbacks, channel "mcp" only.
- {schema_accept_brief_contract()}
- {persist_accept_brief_contract()}
- {workflow_accept_brief_contract()}

This brief is the horizon. The user message is the compiled whole-job brief
(TARGET / STEP 0 INVENTORY / DO / ACCEPTANCE) — not one handle(), one spec,
or one route. A stage wall may hard-stop you so the factory can inspect
what was achieved; that stop is not permission to ship a scaffold.
""".strip()


def writer_system_brief() -> str:
    """Seat JD plus the one gated brief. This is what the coder receives."""
    return kernel_seat_brief("WRITER") + "\n\n" + CODING_AGENT_BRIEF + "\n"
