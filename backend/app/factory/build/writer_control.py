"""Writer hold control (legacy MR. FINANCE seam).

SUPERSEDED (2026-09-15): the live loop is full-pipeline autopilot -
COLLECTOR -> CLONER -> WRITER -> TESTER -> STORE_MANAGER with no
MR. FINANCE pause and no Cursor BA. ``FACTORY_WRITER_REQUIRES_HANDOFF``
is now OPT-IN: explicit ``1``/``true`` re-enables the old hold; unset
(production included) runs straight through. TESTER (acceptance
inspector) still runs after Writer before STORE_MANAGER / N3.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

WRITER_HOLD_ENV = "FACTORY_WRITER_REQUIRES_HANDOFF"
AWAITING_MR_FINANCE_WRITER = "awaiting_mr_finance_writer"
AWAITING_DETAIL = (
    "CLONER complete; awaiting MR. FINANCE to launch Writer "
    "(FACTORY_WRITER_REQUIRES_HANDOFF)"
)


def writer_requires_handoff(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when Floor must stop after CLONER and wait for a launch action.

    Legacy opt-in: the MR. FINANCE hold is out of the loop. Only an
    explicit ``1``/``true``/``yes``/``on`` enables it; unset (production
    included) runs the full pipeline straight through.
    """
    src = env if env is not None else os.environ
    raw = str(src.get(WRITER_HOLD_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def payload_awaits_mr_finance_writer(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    honesty = str(payload.get("honesty") or "")
    outcome = str(payload.get("outcome") or "")
    return honesty == AWAITING_MR_FINANCE_WRITER or outcome == "AWAITING_MR_FINANCE_WRITER"


def status_awaits_mr_finance_writer(status: Any) -> bool:
    if not isinstance(status, Mapping):
        return False
    if payload_awaits_mr_finance_writer(status):
        return True
    return str(status.get("state") or "") == "waiting" and bool(
        status.get("awaiting_mr_finance_writer")
    )


def ledger_awaits_mr_finance_writer(ledger: Any) -> bool:
    """True while the latest terminal is the post-Cloner Writer hold."""
    terminal = None
    try:
        terminal = ledger.terminal_event() if ledger is not None else None
    except Exception:  # noqa: BLE001 — torn ledger is not a hold
        return False
    if terminal is None:
        return False
    return payload_awaits_mr_finance_writer(getattr(terminal, "payload", None) or {})
