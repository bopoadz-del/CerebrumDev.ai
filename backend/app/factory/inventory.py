"""Factory inventory declaration: what the Store actually stocks.

The writer can only be honest about domain depth where the Store carries
domain content. READY_VERTICALS are the verticals the operator has
declared ready for building/testing, each mapped to its real kit pack in
the Store (Cerebrum-Blocks ``block_store/kits/``). Everything else gets an
honest inventory note on the draft — never a silent fabrication of domain
authority (live-factory lesson: veterinary-clinic drafted 8 REUSE
capabilities against generic plumbing with zero veterinary domain
content).

Ground truth (Cerebrum-Blocks block_store/kits/, 2026-09-16):
agriculture, automotive, aviation, construction, education, finance,
finance_ops, hotel_management, hr, insurance, legal, manufacturing,
medical, mep_coordination, oil_gas, pharma, real_estate, retail,
supply_chain, universal_business, universal_kernel, _template.
"""

from __future__ import annotations

from typing import Dict, Optional

#: vertical slug -> Store kit id. The five the operator declared ready.
READY_VERTICALS: Dict[str, str] = {
    "hotel": "hotel_management",
    "hotels": "hotel_management",
    "hotel_management": "hotel_management",
    "hospitality": "hotel_management",
    "insurance": "insurance",
    "construction": "construction",
    "finance": "finance",
    "finance_ops": "finance_ops",
    "retail": "retail",
}

#: Verticals the operator declared OUT of testing: no domain kit, and the
#: Store carries no authoritative domain content for them.
EXCLUDED_VERTICALS = frozenset(
    {
        "medical",
        "medical_clinic",
        "clinic",
        "healthcare",
        "legal",
        "law",
        "law_firm",
        "legal_practice",
        "veterinary",
        "vet",
        "veterinary_clinic",
        "pharma",
        "pharmaceutical",
    }
)


def _slug(vertical: str) -> str:
    return str(vertical or "").strip().replace("-", "_").replace(" ", "_").lower()


def ready_kit(vertical: str) -> Optional[str]:
    """The Store kit for a ready vertical, or None."""
    return READY_VERTICALS.get(_slug(vertical))


def vertical_is_excluded(vertical: str) -> bool:
    return _slug(vertical) in EXCLUDED_VERTICALS


def inventory_drafting_note(vertical: str) -> str:
    """The honest note a draft carries when the vertical is not declared ready.

    Excluded verticals get the stronger wording: no domain kit exists, and
    building one means the writer synthesizes domain authority the Store
    never supplied.
    """
    slug = _slug(vertical)
    if not slug:
        return ""
    if vertical_is_excluded(slug):
        return (
            f"inventory: no domain kit in the Store for '{slug}' — this "
            "vertical is outside the declared-ready set (hotels, insurance, "
            "construction, finance, retail); domain logic will be thin "
            "because the Store supplies no authoritative domain content"
        )
    if slug not in READY_VERTICALS:
        return (
            f"inventory: '{slug}' is not on the declared-ready list (hotels, "
            "insurance, construction, finance, retail); the Store kit for "
            "this vertical is unverified — domain depth not guaranteed"
        )
    return ""
