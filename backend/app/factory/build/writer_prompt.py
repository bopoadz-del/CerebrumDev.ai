"""The writer prompt template — the product (Phase 5.3).

Versioned, filled deterministically per platform from the brief. The same
brief renders the same bytes every time (T5.6): no timestamps, no
randomness, no ambient state. A change to this template is a product
change: bump the version, log it, re-run the determinism test.
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "writer_worker_prompt.v2"

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
- app/block_inputs.py, app/migrations/ (versioned schema revisions)
- tests/ — pytest suite, runnable from the checkout root
- frontend/src/App.tsx, Dockerfile, README.md, requirements.txt
- scripts/release_gate.py

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


def render_writer_prompt(
    blueprint: Any,
    *,
    brief: str = "",
    version: str = PROMPT_VERSION,
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
    return f"<!-- {version} -->\n" + body
