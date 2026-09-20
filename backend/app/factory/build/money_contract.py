"""A platform that computes money may not invent the country it computes for.

FinOps (sess_065fc3eac75c4f62) was a finance platform for a Dubai business.
Its brief named no country, and the coding agent hardcoded UK VAT
(``STANDARD_VAT_RATE = 0.2``) and GBP -- so every net/VAT split it computes
is wrong for the UAE (5%, AED), and it passed 13/13 in Docker because no
check has ever read a tax rate.

The writer prompt (v6) tells the agent to make these settings when the brief
is silent. This is the gate behind that instruction: an instruction the
factory does not check is a suggestion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

MONEY_ASSUMED = "money_assumed_without_a_brief"

#: ISO codes a product might default to. Not exhaustive -- it does not need
#: to be: a code the brief DOES name is what silences the check.
CURRENCY_CODES = frozenset(
    """GBP USD EUR AED SAR QAR KWD BHD OMR JOD EGP TRY INR PKR BDT LKR
    CHF CAD AUD NZD ZAR NGN KES SGD MYR IDR THB PHP JPY CNY HKD KRW
    SEK NOK DKK PLN CZK HUF RON BRL MXN CLP ARS""".split()
)

#: Enough of the world to recognise a brief that HAS placed the business.
COUNTRY_WORDS = frozenset(
    """uae emirates dubai abu dhabi sharjah ajman saudi ksa riyadh jeddah
    qatar doha kuwait bahrain oman muscat jordan amman egypt cairo lebanon
    uk britain british england scotland wales london ireland usa u.s.
    america american canada canadian mexico brazil india indian mumbai delhi
    pakistan bangladesh sri lanka germany german france french spain spanish
    italy italian netherlands dutch belgium sweden norway denmark finland
    poland portugal greece turkey türkiye switzerland austria australia
    australian new zealand south africa nigeria kenya singapore malaysia
    indonesia thailand philippines vietnam china chinese japan japanese
    korea hong kong""".split()
)

#: ``VAT_RATE = 0.2`` / ``STANDARD_TAX = 5`` -- a rate frozen into the source.
_RATE_ASSIGNMENT = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*(?:VAT|TAX|GST|DUTY)[A-Z0-9_]*)\s*=\s*"
    r"(\d+(?:\.\d+)?|\.\d+)\s*(?:#.*)?$",
    re.MULTILINE,
)
#: ``currency = "GBP"`` / ``or 'AED'`` -- a currency frozen into the source.
_CURRENCY_LITERAL = re.compile(r"""['"]([A-Z]{3})['"]""")

_SCANNED = ("app",)
_SKIP_PARTS = ("__pycache__", "vendor", "kits", "tests")


_COUNTRY_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(w) for w in COUNTRY_WORDS), key=len, reverse=True)) + r")\b"
)
_CODE_RE = re.compile(r"\b(" + "|".join(sorted(CURRENCY_CODES)) + r")\b")


def brief_places_the_business(brief: str) -> bool:
    """True when the brief says where this platform operates, or in what money.

    Matched on word boundaries against the USER's brief. An earlier draft
    searched the compiled writer brief (70k characters of factory
    boilerplate and Store inventory) for bare substrings, so "uk" matched
    inside an unrelated word and the check passed the very build it was
    written for.
    """
    text = str(brief or "")
    if not text.strip():
        return False
    return bool(_COUNTRY_RE.search(text.lower()) or _CODE_RE.search(text))


def money_findings(workspace: Path, brief: str) -> List[str]:
    """Hardcoded tax rates / currencies in a product whose brief named none."""
    if brief_places_the_business(brief):
        return []
    findings: List[str] = []
    root = Path(workspace)
    for top in _SCANNED:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            for name, value in _RATE_ASSIGNMENT.findall(source):
                findings.append(
                    f"{rel}: {name} = {value} is a tax rate frozen into the "
                    "source, and the brief names no country -- read it from "
                    "the environment and name it in README"
                )
            for line in source.splitlines():
                # Only where the line is about currency: a bare three-letter
                # string is not an assumption (block ids, status codes, keys).
                if line.lstrip().startswith("#") or "currenc" not in line.lower():
                    continue
                for code in _CURRENCY_LITERAL.findall(line):
                    if code not in CURRENCY_CODES:
                        continue
                    findings.append(
                        f"{rel}: defaults currency to {code!r}, and the brief "
                        "names no country -- read it from the environment and "
                        "name it in README"
                    )
    # One finding per symbol is enough to act on; a long list is noise.
    return sorted(set(findings))[:12]
