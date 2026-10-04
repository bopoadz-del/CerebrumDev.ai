"""A platform that computes money may not invent the country it computes for.

FinOps (sess_065fc3eac75c4f62) was a finance platform for a Dubai business.
Its brief named no country, and the coding agent hardcoded UK VAT
(``STANDARD_VAT_RATE = 0.2``) and GBP -- so every net/VAT split it computes
is wrong for the UAE (5%, AED).

ADVISORY (owner no-hardwiring rule, 2026-10-05). This gate decided both of
its questions by matching words: "the brief names no country" against a list
of country and city names, and "this constant is a tax rate / a currency"
against symbol-name patterns (VAT|TAX|GST|DUTY) and lines that mention
"currency". Neither has a structural equivalent today: the compiled brief
carries no locale field, and a spec's money / currency types normalise to
``float`` with no currency attached. A check with no structural signal is
deleted and its gate goes advisory with the reason stated -- never kept as a
word list. The writer prompt still instructs the agent to make country,
currency and tax rates settings when the brief is silent.

It returns to a veto when the compiled brief carries a structured locale
(country / currency) that a product's money fields can be checked against.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

MONEY_ASSUMED = "money_assumed_without_a_brief"

#: Why the money gate is advisory -- recorded where the gate reports.
MONEY_ADVISORY_REASON = (
    "money_assumed is ADVISORY: neither the compiled brief nor the build's "
    "specs carry a structured country or currency (spec money/currency types "
    "normalise to float), so 'the brief names no country' and 'this constant "
    "is a tax rate' could only be decided by matching words -- removed under "
    "the no-hardwiring rule. Returns to a veto when the brief compiles a "
    "structured locale."
)


def money_advisory() -> str:
    """The stated reason this gate does not veto."""
    return MONEY_ADVISORY_REASON


def money_findings(workspace: Path, brief: str) -> List[str]:  # noqa: ARG001
    """Advisory: no structural signal to judge by, so nothing is refused.

    See MONEY_ADVISORY_REASON. Kept as the gate's entry point so the
    writer_contract wiring is unchanged and the veto can return without a
    second wiring change.
    """
    return []
