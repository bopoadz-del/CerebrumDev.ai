"""The factory refuses a platform that decides which country it is for.

Prompt v6 tells the coding agent to take country, currency and tax rates
from the brief, and to make them settings when the brief is silent. That was
shipped as an instruction with nothing checking it -- and an instruction the
factory does not verify is a suggestion. FinOps (sess_065fc3eac75c4f62)
hardcoded UK VAT (STANDARD_VAT_RATE = 0.2) and GBP for a Dubai business and
passed 13/13 in Docker, because no gate had ever read a tax rate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.money_contract import (
    brief_places_the_business,
    money_findings,
)

SILENT = "Build a finance platform. 40 budget owners, CFO and approvers. Google Drive and Sage."
PLACED = SILENT + " The business is in Dubai and reports in AED."


def _product(root: Path, **files: str) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "brief, placed",
    [
        (SILENT, False),
        (PLACED, True),
        ("payroll for a UK company", True),
        ("invoicing in AED", True),
        ("a platform for Abu Dhabi facilities", True),
        ("", False),
        # The trap that made the first draft of this check vacuous: a bare
        # substring match found "uk" inside an unrelated word.
        ("a sukuk compliance tracker", False),
    ],
)
def test_the_brief_is_read_for_a_country_or_currency(brief, placed):
    assert brief_places_the_business(brief) is placed


def test_a_frozen_tax_rate_is_refused_when_the_brief_is_silent(tmp_path):
    ws = _product(
        tmp_path,
        **{"app/formulas.py": "STANDARD_VAT_RATE = 0.2\n\n\ndef net(x):\n    return x\n"},
    )

    findings = money_findings(ws, SILENT)

    assert len(findings) == 1
    assert "STANDARD_VAT_RATE = 0.2" in findings[0]
    assert "app/formulas.py" in findings[0]


def test_a_defaulted_currency_is_refused_too(tmp_path):
    ws = _product(
        tmp_path,
        **{"app/actions/spend.py": "def h(r):\n    currency = str(r.get('currency') or 'GBP')\n    return currency\n"},
    )

    assert any("GBP" in f for f in money_findings(ws, SILENT))


def test_a_brief_that_places_the_business_settles_it(tmp_path):
    ws = _product(
        tmp_path,
        **{
            "app/formulas.py": "STANDARD_VAT_RATE = 0.05\n",
            "app/actions/spend.py": "currency = 'AED'\n",
        },
    )

    assert money_findings(ws, PLACED) == []


def test_reading_the_rate_from_the_environment_is_what_was_asked_for(tmp_path):
    ws = _product(
        tmp_path,
        **{
            "app/formulas.py": (
                "import os\n\n"
                "VAT_RATE = float(os.environ['VAT_RATE'])\n"
                "CURRENCY = os.environ['CURRENCY']\n"
            )
        },
    )

    assert money_findings(ws, SILENT) == []


def test_vendored_and_test_code_is_not_the_products_assumption(tmp_path):
    ws = _product(
        tmp_path,
        **{
            "app/vendor/blocks/x.py": "STANDARD_VAT_RATE = 0.2\n",
            "app/tests/test_x.py": "STANDARD_VAT_RATE = 0.2\n",
        },
    )

    assert money_findings(ws, SILENT) == []


def test_a_three_letter_string_that_is_not_about_money_is_left_alone(tmp_path):
    ws = _product(
        tmp_path,
        **{"app/dispatch.py": "BLOCK = 'USD'  # a block id that happens to look like a code\n"},
    )

    assert money_findings(ws, SILENT) == []


def test_the_writer_gate_refuses_by_name(tmp_path):
    """The wiring: the finding must stop the pass, not just be computed."""
    import inspect

    from app.factory.build import gates

    src = inspect.getsource(gates.gate_writer_contract)
    assert "money_findings(ctx.workspace, ctx.brief)" in src
    assert "reason=MONEY_ASSUMED" in src


def test_the_gate_context_carries_the_users_brief():
    """Not the compiled writer prompt: that is 70k characters of factory
    boilerplate, and searching it for a country is how the first draft of
    this check passed the build it was written for."""
    import inspect

    from app.factory.build import runner
    from app.factory.build.gates import GateContext

    assert "brief" in GateContext.__dataclass_fields__
    assert 'kwargs["brief"]' in inspect.getsource(runner.RoleRunner._gate_context)


class TestTheWriterIsNotToldHowToWriteIt:
    """The gate judges whether the country was decided for the customer.

    Not how the code is spelled. A value the operator can override is
    configuration, and the agent picks the shape: a bare env read, an env
    read with a default, a named default handed to getenv, a settings class.
    An earlier draft refused the last two -- good engineering, refused.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "import os\nVAT_RATE = float(os.environ['VAT_RATE'])\n",
            "import os\nVAT_RATE = float(os.getenv('VAT_RATE', '0.05'))\n",
            "import os\nDEFAULT_VAT_RATE = 0.05\nVAT_RATE = float(os.getenv('VAT_RATE', DEFAULT_VAT_RATE))\n",
            "import os\n\n\nclass Settings:\n    vat_rate = float(os.environ.get('VAT_RATE', 0))\n",
            "import os\ncurrency = os.getenv('CURRENCY', 'AED')\n",
            "import os\nCURRENCY = os.environ.get('CURRENCY', 'GBP')\n",
        ],
        ids=["env", "env-default", "named-default", "settings-class", "currency-env", "currency-env-default"],
    )
    def test_operator_overridable_money_is_allowed(self, tmp_path, source):
        ws = _product(tmp_path, **{"app/formulas.py": source})

        assert money_findings(ws, SILENT) == [], source

    @pytest.mark.parametrize(
        "source",
        [
            "STANDARD_VAT_RATE = 0.2\n",
            "currency = 'GBP'\n",
        ],
        ids=["frozen-rate", "frozen-currency"],
    )
    def test_a_value_the_operator_cannot_reach_is_still_refused(self, tmp_path, source):
        ws = _product(tmp_path, **{"app/formulas.py": source})

        assert money_findings(ws, SILENT), source
