"""The country and currency the USER declared on the Floor.

Typed fields beside the vertical picker, validated by SHAPE only (2 / 3
uppercase letters) -- no list of countries or currencies exists anywhere in
the Factory. They persist on the session's product design and are copied onto
the stored blueprint (``blueprint.locale``), which every build path reads, so
the money contract judges the build against what the user said and nothing
else. Never inferred from the brief's prose.
"""

from __future__ import annotations

from typing import Any, Optional

from app.factory.build.money_contract import declared_locale, shaped_country, shaped_currency


def apply_locale_choice(pd: Any, country: Optional[str], currency: Optional[str]) -> None:
    """Record a typed answer. ``None`` leaves a field as it was; an empty
    string clears it back to undeclared."""
    if country is not None:
        pd.country = shaped_country(country)
    if currency is not None:
        pd.currency = shaped_currency(currency)
    sync_blueprint_locale(pd)


def sync_blueprint_locale(pd: Any) -> bool:
    """Make the stored blueprint carry exactly the declared pair. Returns
    True when it changed."""
    blueprint = getattr(pd, "blueprint", None)
    if not isinstance(blueprint, dict):
        return False
    wanted = declared_locale(getattr(pd, "country", None), getattr(pd, "currency", None))
    if blueprint.get("locale") == wanted:
        return False
    blueprint["locale"] = wanted
    pd.blueprint = blueprint
    return True
