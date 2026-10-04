"""The money gate is ADVISORY, with its reason stated (owner no-hardwiring rule).

It used to refuse a platform that froze a tax rate or currency when the brief
named no country -- FinOps (sess_065fc3eac75c4f62) hardcoded UK VAT and GBP
for a Dubai business. Both of its decisions were word matches (a list of
country names over the brief; VAT/TAX/GST symbol patterns and "currency"
lines over the source), and neither has a structural equivalent: the compiled
brief has no locale field and spec money types normalise to float. A check
with no structural signal is deleted and its gate goes advisory with the
reason in its own record. These tests pin exactly that, so the loss is
visible rather than silent.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build import money_contract
from app.factory.build.money_contract import (
    MONEY_ADVISORY_REASON,
    money_advisory,
    money_findings,
)


def _product(root: Path, **files: str) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def test_the_gate_is_advisory_and_says_why():
    reason = money_advisory()
    assert reason == MONEY_ADVISORY_REASON
    assert "ADVISORY" in reason
    assert "structured" in reason


def test_the_finops_shape_is_no_longer_refused(tmp_path):
    """The stated loss: a frozen rate and currency are not refused while the
    brief carries no structured locale."""
    ws = _product(
        tmp_path,
        **{
            "app/formulas.py": "STANDARD_VAT_RATE = 0.2\n",
            "app/actions/spend.py": "currency = 'GBP'\n",
        },
    )
    assert money_findings(ws, "Build a finance platform.") == []


def test_no_word_list_survives_in_the_module():
    """No country/city names, no currency-code list, no symbol-name pattern."""
    for name in ("COUNTRY_WORDS", "CURRENCY_CODES", "_RATE_ASSIGNMENT", "_COUNTRY_RE",
                 "_CODE_RE", "_CURRENCY_LITERAL", "brief_places_the_business"):
        assert not hasattr(money_contract, name), name


def test_the_writer_gate_wiring_is_kept_for_the_veto_to_return():
    import inspect

    from app.factory.build import gates

    src = inspect.getsource(gates.gate_writer_contract)
    assert "money_findings(ctx.workspace, ctx.brief)" in src
    assert "reason=MONEY_ASSUMED" in src


def test_the_gate_context_carries_the_users_brief():
    import inspect

    from app.factory.build import runner
    from app.factory.build.gates import GateContext

    assert "brief" in GateContext.__dataclass_fields__
    assert 'kwargs["brief"]' in inspect.getsource(runner.RoleRunner._gate_context)
