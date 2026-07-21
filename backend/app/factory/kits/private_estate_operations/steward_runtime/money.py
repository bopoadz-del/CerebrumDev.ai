"""Decimal money helpers — no float for financial fields."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional, Union

MONEY_SCALE = Decimal("0.0001")
MONEY_QUANT = Decimal("0.0001")


def parse_money(value: Any) -> Optional[Decimal]:
    """Parse a money value into a quantized Decimal, or None."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def serialize_money(value: Optional[Union[Decimal, float, int, str]]) -> Optional[str]:
    """Serialize money for JSON API responses as a fixed-scale decimal string."""
    parsed = parse_money(value)
    if parsed is None:
        return None
    return format(parsed, "f")


def money_add(left: Optional[Decimal], right: Optional[Decimal]) -> Optional[Decimal]:
    """Exact decimal addition for budget math."""
    if left is None and right is None:
        return None
    total = (left or Decimal("0")) + (right or Decimal("0"))
    return total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
