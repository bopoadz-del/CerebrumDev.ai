"""The writer prompt template — the product (Phase 5.3).

Versioned, filled deterministically per platform from the brief. The same
brief renders the same bytes every time (T5.6): no timestamps, no
randomness, no ambient state. A change to this template is a product
change: bump the version, log it, re-run the determinism test.
"""

from __future__ import annotations

from typing import Any

#: Version log.
#: v4 -- PROCESSES section. The agent has a shell in the factory's own
#:   container. Live: the factory server received a clean SIGTERM 25s after
#:   the agent "ran the writer behaviour probe end to end" (FleetOps, no deploy,
#:   no OOM; same signature the evening before) and the build died with it.
#:   The agent is now told the factory is a uvicorn process on its machine and
#:   may only stop PIDs it started.
#: v3 -- PERSISTENCE section. v2 asked for an ``app/migrations/`` package and
#:   never mentioned store/backup/alembic, while the data-lifecycle suite
#:   TESTER stamps imports ``app.backup``, ``app.store`` and the
#:   ``app/migrations.py`` module -- the agent had to reverse-engineer that
#:   contract from red tests, one rework round per file. v3 names what the
#:   factory backfills (data_lifecycle.platform_substrate) and what the agent
#:   owns (store.py, 0001_baseline), with the exact surface the suite calls.
PROMPT_VERSION = "writer_worker_prompt.v4"

_TEMPLATE = """You are the WRITER role of the CerebrumDev factory, manufacturing a
governed platform. Work headless in this checkout. Produce real, runnable
code — no stubs on auth, tenancy, retrieval, formulas, LLM, or export
paths; a feature you cannot implement is a named blocker, never a stub
that passes.

PRODUCT
- product_id: {product_id}
- product_name: {product_name}
- vertical: {vertical}
- summary: {summary}

BRIEF
{brief}

TENANCY
- One tenant per request, always. All corpus access goes through the
  tenant store resolved from the authenticated principal — never a
  client-supplied name.

AUTHORITY
- Layers: 1 certified > 2 documents > 3 formulas > 4 procedures, as
  versioned data (precedence.v1). The model is told the winner; it never
  chooses. Every answer carries divergence records and per-claim labels.

OUTPUT
Write the platform into this checkout, then report the files you wrote
and the artifacts you authored. The factory grades this exact tree — a
missing file is a missing gate, so write it all under this checkout root:

- app/main.py (FastAPI app factory + /health)
- app/models.py exporting ``MODELS``: dict of capability -> model class,
  each class with ``FIELDS`` and ``from_dict``/``to_dict``
- app/actions/<capability>.py — one module per capability, each exporting
  ``CAPABILITY_ID`` and ``handle(payload) -> dict``
- app/routers/ — HTTP routes over the actions
- app/tenancy.py, app/security.py, app/authority.py (precedence.v1),
  app/retrieval.py, app/formulas.py, app/llm.py
- app/block_inputs.py
- app/store.py and alembic/versions/0001_baseline.py (see PERSISTENCE)
- tests/ — pytest suite, runnable from the checkout root
- frontend/src/App.tsx, Dockerfile, README.md, requirements.txt
- scripts/release_gate.py

PERSISTENCE (the factory's data-lifecycle suite grades this contract):
The factory writes the schema-independent substrate itself, after your
pass, wherever you have not: app/migrations.py (alembic wrappers:
upgrade_head, upgrade_to, downgrade, current_revision), app/backup.py,
alembic.ini, alembic/env.py, alembic/script.py.mako,
alembic/versions/0002_lifecycle_audit.py, scripts/entrypoint.sh. Do not
create an app/migrations/ package -- it would shadow app/migrations.py.
You own the two files that carry the entity schema:

- alembic/versions/0001_baseline.py with ``revision = "0001_baseline"``
  and ``down_revision = None``; one literal op.create_table("<entity>")
  call per entity, each table carrying a ``tenant_id`` column.
  0002_lifecycle_audit revises 0001_baseline; stack any further revision
  of yours on top of 0002_lifecycle_audit.
- app/store.py over SQLite at STORAGE_PATH, exposing
  ``SQLITE_BUSY_TIMEOUT_MS``, ``FASTAPI_SYNC_THREADPOOL``, ``db_path()``,
  ``connect()``, ``save(entity, record, tenant_id)``,
  ``get(entity, record_id, tenant_id)``, ``list_all(entity, tenant_id)``.
  connect() sets ``PRAGMA journal_mode=WAL`` and a busy_timeout and never
  issues CREATE TABLE -- schema belongs to alembic, not connect time.

PROCESSES (you share this machine with the factory that is running you):
- NEVER stop processes by name or pattern: no pkill, killall, "kill -9 -1",
  "fuser -k", and no killing of uvicorn / python / gunicorn / node in general.
  The factory's own server is a uvicorn process on this machine; a name-based
  kill stops the factory, and your build dies with it.
- Stop ONLY processes you started yourself, by the exact PID you captured when
  you started them. If you did not record a PID, leave the process alone.
- Never bind or probe the port in $PORT -- it belongs to the factory. Prefer
  in-process test clients (FastAPI TestClient) over starting a server at all;
  if you must start one, use a high port of your own and stop it by PID.

PROGRESS LOG (the operator watches this file live):
After EVERY completed step — before starting the next — append exactly
one line to docs/writer_progress.log in this format:

    STEP <n>: <one-line summary of what you just did>

Start at STEP 1 and number strictly upward. The factory streams this file
to the build monitor; a silent pass looks like a hang, so update it even
for small steps (files written, models emitted, tests added).

A zero-artifact pass is refused (writer_no_output).

AUTHORSHIP STAMP (mandatory — the factory's disk-level artifact gate
counts it): every action handler you author must carry this exact line in
its module docstring:

    Written by the factory WRITER role (codewhale exec)
"""


_RESUME_PREFACE = """RESUME -- READ THIS FIRST.
A previous pass of yours on THIS checkout was interrupted: the factory
restarted underneath you. Your files are still here. Do NOT start over.
1. Read docs/writer_progress.log -- it is your own record of the steps done.
2. Check what is actually on disk against it (a step may have been cut off
   mid-write: verify the last file you were writing parses).
3. Continue from the first step that is not done, and keep numbering STEP
   lines upward from the last number in the log.
Everything below is the original brief, unchanged.

"""


def render_writer_prompt(
    blueprint: Any,
    *,
    brief: str = "",
    version: str = PROMPT_VERSION,
    resume: bool = False,
) -> str:
    """Fill the template from the brief. Deterministic by construction.

    Brief/summary text is inserted verbatim: str.format interprets braces
    only in the template, never in values, so user/model content passes
    through untouched.
    """
    product_id = getattr(blueprint, "product_id", "") or ""
    product_name = getattr(blueprint, "product_name", "") or ""
    vertical = getattr(blueprint, "vertical", "") or ""
    summary = getattr(blueprint, "summary", "") or ""
    body = _TEMPLATE.format(
        product_id=product_id,
        product_name=product_name,
        vertical=vertical,
        summary=summary,
        brief=(brief or "").strip(),
    )
    if resume:
        body = _RESUME_PREFACE + body
    return f"<!-- {version} -->\n" + body
