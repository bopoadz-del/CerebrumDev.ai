"""JSON-Schema declarations and normalization helpers for Retail actions.

Schemas follow the repo convention of plain JSON-Schema dicts (see
``app/core/schema_registry.py``). Handlers operate on already-retrieved
records/rows supplied by the host runtime; the action contract keeps them
domain-neutral.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Shared fragments
# ---------------------------------------------------------------------------

_RECORD_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_id": {"type": "string"},
        "document_id": {"type": "string"},
        "filename": {"type": "string"},
        "page": {"type": "integer"},
        "sheet": {"type": "string"},
        "row_start": {"type": "integer"},
        "row_end": {"type": "integer"},
        "chunk_id": {"type": "string"},
        "type": {"type": "string"},
        "score": {"type": "number"},
    },
    "required": ["text"],
}

_EVIDENCE_OUTPUT = {
    "type": "array",
    "items": {"type": "object"},
}

# ---------------------------------------------------------------------------
# A. generate_operations_brief
# ---------------------------------------------------------------------------

OPERATIONS_BRIEF_INPUT = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "enum": ["daily", "weekly"]},
        "store": {"type": "string"},
        "region": {"type": "string"},
        "date_range": {
            "type": "object",
            "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
        },
        "focus_areas": {"type": "array", "items": {"type": "string"}},
        "records": {"type": "array", "items": _RECORD_SCHEMA},
    },
    "required": ["period"],
}

OPERATIONS_BRIEF_OUTPUT = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "priority_actions": {"type": "array", "items": {"type": "object"}},
        "inventory_signals": {"type": "array", "items": {"type": "object"}},
        "promotion_signals": {"type": "array", "items": {"type": "object"}},
        "supplier_signals": {"type": "array", "items": {"type": "object"}},
        "incidents": {"type": "array", "items": {"type": "object"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "evidence": _EVIDENCE_OUTPUT,
    },
    "required": [
        "executive_summary",
        "priority_actions",
        "inventory_signals",
        "promotion_signals",
        "supplier_signals",
        "incidents",
        "missing_information",
        "evidence",
    ],
}

# ---------------------------------------------------------------------------
# B. analyse_inventory_risk
# ---------------------------------------------------------------------------

INVENTORY_RISK_INPUT = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {"type": "object"}},
        "records": {"type": "array", "items": _RECORD_SCHEMA},
        "thresholds": {"type": "object"},
        "store": {"type": "string"},
    },
}

INVENTORY_RISK_OUTPUT = {
    "type": "object",
    "properties": {
        "summary": {"type": "object"},
        "stockout_risks": {"type": "array", "items": {"type": "object"}},
        "overstock_risks": {"type": "array", "items": {"type": "object"}},
        "slow_moving_items": {"type": "array", "items": {"type": "object"}},
        "anomalies": {"type": "array", "items": {"type": "object"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "evidence": _EVIDENCE_OUTPUT,
    },
    "required": [
        "summary",
        "stockout_risks",
        "overstock_risks",
        "slow_moving_items",
        "anomalies",
        "assumptions",
        "missing_fields",
        "evidence",
    ],
}

# ---------------------------------------------------------------------------
# C. check_promotion_compliance
# ---------------------------------------------------------------------------

PROMOTION_COMPLIANCE_INPUT = {
    "type": "object",
    "properties": {
        "rules": {"type": "array", "items": {"type": "object"}},
        "rule_records": {"type": "array", "items": _RECORD_SCHEMA},
        "evidence": {"type": "array", "items": _RECORD_SCHEMA},
        "campaign": {"type": "string"},
        "store": {"type": "string"},
    },
}

PROMOTION_COMPLIANCE_OUTPUT = {
    "type": "object",
    "properties": {
        "compliant": {"type": "array", "items": {"type": "object"}},
        "non_compliant": {"type": "array", "items": {"type": "object"}},
        "missing_evidence": {"type": "array", "items": {"type": "object"}},
        "rule_results": {"type": "array", "items": {"type": "object"}},
        "summary": {"type": "object"},
        "evidence": _EVIDENCE_OUTPUT,
    },
    "required": [
        "compliant",
        "non_compliant",
        "missing_evidence",
        "rule_results",
        "summary",
        "evidence",
    ],
}


# ---------------------------------------------------------------------------
# Inventory field normalization
# ---------------------------------------------------------------------------

# Canonical field -> accepted aliases (all matched case-insensitively, with
# spaces/underscores/hyphens collapsed).
INVENTORY_ALIASES: Dict[str, List[str]] = {
    "sku": ["sku", "item", "item_code", "product_code", "productid", "product_id"],
    "product_name": ["product_name", "product", "name", "description", "item_name"],
    "on_hand": ["on_hand", "onhand", "quantity_on_hand", "qty_on_hand", "stock", "quantity", "qty"],
    "reorder_point": ["reorder_point", "rop", "reorder_level", "reorder"],
    "safety_stock": ["safety_stock", "safety", "min_stock", "minimum_stock"],
    "average_daily_sales": [
        "average_daily_sales", "avg_daily_sales", "ads", "daily_sales",
        "avg_sales_per_day", "average_sales",
    ],
    "inbound_quantity": ["inbound_quantity", "inbound", "on_order", "incoming", "po_quantity"],
    "last_count_quantity": ["last_count_quantity", "last_count", "previous_count", "prior_count"],
    "current_count_quantity": ["current_count_quantity", "current_count", "counted", "physical_count"],
    "unit_cost": ["unit_cost", "cost", "cost_price", "unit_price", "price"],
    "store": ["store", "location", "site", "branch", "store_id"],
    "date": ["date", "as_of", "as_of_date", "count_date", "report_date"],
}

_NUMERIC_FIELDS = {
    "on_hand", "reorder_point", "safety_stock", "average_daily_sales",
    "inbound_quantity", "last_count_quantity", "current_count_quantity", "unit_cost",
}


def _canon(key: str) -> str:
    return "".join(ch for ch in key.lower().strip() if ch.isalnum())


_ALIAS_LOOKUP = {
    _canon(alias): canonical
    for canonical, aliases in INVENTORY_ALIASES.items()
    for alias in aliases
}


def _to_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    try:
        return float(text)
    except ValueError:
        return None


def normalize_inventory_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map a raw inventory row to canonical fields using accepted aliases."""
    normalized: Dict[str, Any] = {}
    for key, value in row.items():
        canonical = _ALIAS_LOOKUP.get(_canon(str(key)))
        if not canonical:
            continue
        if canonical in _NUMERIC_FIELDS:
            num = _to_number(value)
            if num is not None:
                normalized[canonical] = num
        else:
            normalized[canonical] = value
    return normalized


def present_fields(rows: List[Dict[str, Any]]) -> set:
    fields: set = set()
    for row in rows:
        fields.update(row.keys())
    return fields
