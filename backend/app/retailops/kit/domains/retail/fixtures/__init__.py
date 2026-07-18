"""Deterministic evaluation fixtures for Retail actions (fictional data only).

These fixtures back the ``evaluation_fixtures`` referenced by each ActionSpec
and are used by the Retail action tests. All data is fictional (Northstar
Global Retail) and contains no real client/employer information.
"""

from __future__ import annotations

from typing import Any, Dict

OPERATIONS_BRIEF_HAPPY: Dict[str, Any] = {
    "period": "daily",
    "store": "Northstar Riyadh Olaya",
    "records": [
        {
            "text": "Incident report: A customer slipped near the chilled aisle at 09:14. "
            "First aid administered. Wet-floor signage was missing.",
            "filename": "incident_2024_09_02.pdf",
            "document_id": "doc_incident_1",
            "page": 1,
        },
        {
            "text": "Supplier notice: Chilled dairy shipment from Al-Marai delayed 24h due to "
            "cold-chain inspection. Affects SKUs DAIRY-1001..1009.",
            "filename": "supplier_notice_dairy.pdf",
            "document_id": "doc_supplier_1",
        },
        {
            "text": "Inventory record: SKU BEV-2201 on-hand 4 units below reorder point of 20; "
            "possible stock discrepancy flagged during morning count.",
            "filename": "inventory_note.txt",
            "document_id": "doc_inv_1",
        },
        {
            "text": "Merchandising instruction: Endcap display for the Ramadan promotion must "
            "show approved signage and price cards by store open.",
            "filename": "promo_display.md",
            "document_id": "doc_promo_1",
        },
    ],
}

OPERATIONS_BRIEF_NO_RECORDS: Dict[str, Any] = {"period": "weekly", "records": []}

INVENTORY_RISK_HAPPY: Dict[str, Any] = {
    "rows": [
        {"SKU": "BEV-2201", "Product": "Sparkling Water 500ml", "On Hand": 4, "Reorder Point": 20, "Avg Daily Sales": 6, "Store": "Riyadh Olaya"},
        {"SKU": "SNK-3310", "Product": "Roasted Almonds 200g", "On Hand": 900, "Reorder Point": 50, "Avg Daily Sales": 3, "Store": "Riyadh Olaya"},
        {"SKU": "DAIRY-1001", "Product": "Full Cream Milk 1L", "on_hand": 120, "reorder_point": 40, "average_daily_sales": 30, "store": "Riyadh Olaya"},
        {"sku": "HOME-7788", "product_name": "Scented Candle", "on_hand": 60, "average_daily_sales": 0, "store": "Riyadh Olaya"},
        {"sku": "TOY-5501", "product_name": "Building Blocks", "on_hand": -3, "reorder_point": 10, "average_daily_sales": 2, "last_count": 25, "current_count": 40},
    ],
    "records": [
        {"text": "Daily inventory export for Riyadh Olaya", "filename": "inventory_riyadh.csv", "document_id": "doc_inv_csv"}
    ],
}

INVENTORY_RISK_NO_DATA: Dict[str, Any] = {"rows": [{"note": "no stock columns here"}]}

PROMOTION_COMPLIANCE_HAPPY: Dict[str, Any] = {
    "rules": [
        {"rule_id": "PROMO-1", "requirement": "Ramadan endcap must display approved signage.", "keywords": ["endcap", "signage"]},
        {"rule_id": "PROMO-2", "requirement": "Promotional price cards must be visible on shelf.", "keywords": ["price", "cards"]},
        {"rule_id": "PROMO-3", "requirement": "Restricted items must not appear in the promotion.", "keywords": ["restricted", "tobacco"]},
    ],
    "evidence": [
        {
            "text": "Store photo review confirms the Ramadan endcap displays approved signage and "
            "promotional price cards are visible on the shelf.",
            "filename": "store_photos_review.pdf",
            "document_id": "doc_evidence_1",
        }
    ],
    "campaign": "Ramadan 2024",
}

PROMOTION_COMPLIANCE_NO_RULES: Dict[str, Any] = {
    "rules": [],
    "evidence": [{"text": "Store looks compliant.", "filename": "note.txt"}],
}
