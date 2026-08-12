"""Wave 2: Decimal money scaffolding tests."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / "backend" / "app" / "factory" / "kits" / "private_estate_operations" / "steward_runtime"


def _load_money():
    spec = importlib.util.spec_from_file_location("steward_money_test", KIT / "money.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_money_quantizes():
    money = _load_money()
    assert money.parse_money("420.0") == Decimal("420.0000")
    assert money.parse_money(1100) == Decimal("1100.0000")


def test_serialize_money_returns_string():
    money = _load_money()
    assert money.serialize_money(Decimal("420.0000")) == "420.0000"


def test_money_add_exact():
    money = _load_money()
    assert money.money_add(Decimal("0.1"), Decimal("0.2")) == Decimal("0.3000")


def test_models_use_numeric_not_float():
    text = (KIT / "models.py").read_text(encoding="utf-8")
    assert "Numeric(20, 4)" in text
    assert "Mapped[Optional[Decimal]]" in text
    assert "Float" not in text.split("FacilityAsset")[1].split("class FleetVehicle")[0]
