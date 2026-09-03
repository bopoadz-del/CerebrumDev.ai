"""When a Factory Floor run should continue past code-cycle SUCCESS.

The residential-lettings export finished in ~14 minutes with
``rework_used=0`` and ``pilot_ready=false``. That was not a hang: the Floor
budget is a 20–30 minute *code* phase, the runner writes ``RUN_SUCCEEDED``
as soon as ``pytest -m "not pilot"`` is green, and the UI treated that
SUCCESS as "Finished / Download ready".

A Store-green platform is a *pilot* cycle on the same workspace (pytest
``-m pilot``, WRITER rework of failing capabilities, STORE ops). This
module is the single gate for opening that cycle automatically when a
factory coder key is configured, instead of parking on a thin prototype.
"""

from __future__ import annotations

import os

AUTO_PILOT_ENV = "FACTORY_AUTO_PILOT"
KEYED_PATH_CI_ENV = "KEYED_PATH_CI"

#: Combined Floor wall when the run will continue into a Store-green cycle.
#: The code-only default (30 min) is what produced the 13.7 min SUCCESS.
AUTO_PILOT_WALL_CLOCK_S = 7200.0
AUTO_PILOT_MAX_REWORK = 3
#: Minimum remaining wall granted when a code cycle auto-opens pilot mid-run.
PILOT_MIN_REMAINING_S = 5400.0


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _falsey(raw: str) -> bool:
    return raw.strip().lower() in {"0", "false", "no", "off"}


def factory_llm_ready() -> bool:
    """True when the factory coder is enabled and a non-mock key is set."""
    from app.factory.coder import coder_enabled

    if not coder_enabled():
        return False
    from app.core.llm_config import get_factory_llm_config

    cfg = get_factory_llm_config()
    if cfg.get("error") or cfg.get("mock"):
        return False
    return bool(cfg.get("api_key"))


def factory_auto_pilot_enabled() -> bool:
    """Continue a Floor run into a pilot cycle after code-phase SUCCESS.

    Explicit ``FACTORY_AUTO_PILOT=0`` keeps the historical code-only stop.
    Explicit ``1`` forces the continue (tests). Unset means: continue when
    the factory coder has a real key. ``KEYED_PATH_CI=1`` stays code-only
    so the stub-key CI job cannot open a Store-green cycle.
    """
    raw = os.getenv(AUTO_PILOT_ENV, "").strip()
    if raw and _falsey(raw):
        return False
    if os.getenv(KEYED_PATH_CI_ENV, "").strip() and not (raw and _truthy(raw)):
        return False
    if raw and _truthy(raw):
        return True
    return factory_llm_ready()
