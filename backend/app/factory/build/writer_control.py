"""MR. FINANCE control of WRITER after Cloner handoff.

CHADi lock (2026-09-14): Floor must not auto-enter WRITER / cli-pivot BA
in the same Generate/Continue autopilot that just finished CLONER.
``handoff_after_cloner`` delivers the frozen command; MR. FINANCE launches
Writer. TESTER (acceptance inspector) still runs after Writer — including
cli-pivot — before STORE_MANAGER / N3.

``FACTORY_WRITER_REQUIRES_HANDOFF`` defaults ON outside unit tests
(fail-closed for production). Unset under ``ENV=test`` or pytest keeps
prior full-pipeline autopilot so existing runner tests stay intact.
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
    """True when Floor must stop after CLONER and wait for MR. FINANCE.

    Explicit ``0``/``false`` disables. Explicit ``1``/``true`` enables.
    When unset: ON for production/dev; OFF under ``ENV=test`` or pytest
    (``PYTEST_CURRENT_TEST``) so unit tests keep prior autopilot unless
    they opt in.
    """
    src = env if env is not None else os.environ
    raw = str(src.get(WRITER_HOLD_ENV) or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    if str(src.get("ENV") or "").strip().lower() == "test":
        return False
    if src.get("PYTEST_CURRENT_TEST"):
        return False
    return True


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
