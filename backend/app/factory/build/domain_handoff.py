"""Vertical resolution for a generated platform, plus C-BRIEF staging.

Resolve the business vertical a customer's platform is being built FOR
(``finance`` / ``finance_ops``, ``automotive``, or a slug derived from any
other product id) and freeze the compiled C-BRIEF at
``docs/coder_brief.md`` so the WRITER — which runs on CodeWhale (DeepSeek)
via ``codewhale exec`` — reads the block contracts, REUSE inventory and gap
list instead of authoring blind.

Unknown verticals still resolve: ``airline``, ``hotelops``, … or
``general``. ``finance`` here is a customer BUSINESS VERTICAL (a generated
finance-ops platform), never an agent role. This module makes no network
calls and publishes to no external agent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

BRIEF_REL = Path("docs") / "coder_brief.md"
# Bounded slug length keeps a derived domain id short and comparable.
_DOMAIN_SLUG_MAX = 40
_GENERIC_SEGMENTS = frozenset(
    {
        "ops",
        "delivery",
        "management",
        "platform",
        "product",
        "app",
        "v1",
        "v2",
        "core",
        "kit",
        "demo",
        "test",
        "session",
        "workspace",
        "the",
        "and",
    }
)


@dataclass(frozen=True)
class DomainSpec:
    """Canonical vertical for a generated platform: payload id + vertical."""

    domain: str
    vertical: str


FINANCE_SPEC = DomainSpec(domain="finance", vertical="finance_ops")
# Car dealership / automotive resolve to one vertical: automotive
AUTOMOTIVE_SPEC = DomainSpec(domain="automotive", vertical="automotive")

FINANCE_VERTICALS = frozenset(
    {
        "finance",
        "finance_ops",
        "finance-ops",
        "financeops",
    }
)
AUTOMOTIVE_VERTICALS = frozenset(
    {
        "car_dealership",
        "car-dealership",
        "cardealership",
        "automotive",
        "auto_dealership",
        "auto-dealership",
        "autodealership",
        "dealership",
    }
)
_SPECS: tuple[tuple[frozenset[str], DomainSpec], ...] = (
    (FINANCE_VERTICALS, FINANCE_SPEC),
    (AUTOMOTIVE_VERTICALS, AUTOMOTIVE_SPEC),
)
SPEC_BY_DOMAIN: Dict[str, DomainSpec] = {
    FINANCE_SPEC.domain: FINANCE_SPEC,
    AUTOMOTIVE_SPEC.domain: AUTOMOTIVE_SPEC,
}
def _norm(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def _token_variants(raw: str) -> List[str]:
    n = _norm(raw)
    if not n:
        return []
    return [
        n,
        n.replace("-", "_"),
        n.replace("_", "-"),
        n.replace("-", "").replace("_", ""),
    ]


def _token_matches(item: str, verticals: frozenset[str]) -> bool:
    if not item:
        return False
    if item in verticals:
        return True
    underscored = item.replace("-", "_")
    collapsed = item.replace("-", "").replace("_", "")
    return underscored in verticals or collapsed in verticals


def _slug_domain(raw: str) -> str:
    """Safe domain slug (lowercase, hyphenated, bounded)."""
    n = _norm(raw)
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in n)
    cleaned = cleaned.replace("_", "-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:_DOMAIN_SLUG_MAX]


def _domain_id_from_token(raw: str) -> str:
    """Sane slug from a product_id / vertical / folder name.

    ``airline-delivery-management`` → ``airline``;
    ``air-ops`` / ``air_ops`` → ``air-ops``;
    ``hotelops`` → ``hotelops``;
    empty / punctuation-only → ``""``.
    """
    slug = _slug_domain(raw)
    if not slug:
        return ""
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    meaningful = [p for p in parts if p not in _GENERIC_SEGMENTS]
    if not meaningful:
        return slug
    head = meaningful[0]
    if len(head) >= 4:
        return head
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return head


def _make_derived_spec(
    domain_id: str, *, vertical: Optional[str] = None
) -> DomainSpec:
    slug = _slug_domain(domain_id) or "general"
    if slug in SPEC_BY_DOMAIN:
        return SPEC_BY_DOMAIN[slug]
    vert_raw = str(vertical or "").strip()
    vert = _slug_domain(vert_raw) if vert_raw else slug
    if not vert:
        vert = slug
    return DomainSpec(domain=slug, vertical=vert)


def _known_spec_for_token(item: str) -> Optional[DomainSpec]:
    if not item:
        return None
    for aliases, spec in _SPECS:
        if _token_matches(item, aliases):
            return spec
        slug = _slug_domain(item)
        parts = [p for p in slug.split("-") if p]
        if parts and _token_matches(parts[0], aliases):
            return spec
        if len(parts) >= 2:
            joined_hyphen = f"{parts[0]}-{parts[1]}"
            joined_under = f"{parts[0]}_{parts[1]}"
            if _token_matches(joined_hyphen, aliases) or _token_matches(
                joined_under, aliases
            ):
                return spec
    return None


def _source_fields(
    output_dir: Path | str,
    *,
    product_id: Optional[str] = None,
    vertical: Optional[str] = None,
    blueprint: Any = None,
) -> List[tuple[str, str]]:
    """Raw handoff tokens in resolution order: vertical, domain, product_id."""
    ordered: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, val: Any) -> None:
        if val is None:
            return
        raw = str(val).strip()
        if not raw:
            return
        key = (kind, _norm(raw))
        if key in seen:
            return
        seen.add(key)
        ordered.append((kind, raw))

    add("vertical", vertical)
    if blueprint is not None:
        for attr in ("vertical", "domain", "product_id"):
            val = getattr(blueprint, attr, None)
            if val is None and isinstance(blueprint, Mapping):
                val = blueprint.get(attr)
            add(attr if attr != "domain" else "domain", val)
    add("product_id", product_id)
    root = Path(output_dir)
    for rel in (
        Path("docs") / "blueprint" / "product_blueprint.json",
        Path("docs") / "product_blueprint.json",
        Path("docs") / "intake_blueprint.json",
    ):
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, Mapping):
            continue
        for key in ("vertical", "domain", "product_id"):
            add(key, data.get(key))
    add("product_id", root.name)
    return ordered


def detect_domain(
    output_dir: Path | str,
    *,
    product_id: Optional[str] = None,
    vertical: Optional[str] = None,
    blueprint: Any = None,
) -> DomainSpec:
    """Resolve the vertical spec for any workspace. Never returns None.

    Known finance / automotive aliases resolve to their canonical spec;
    everything else gets a derived slug (or ``general``).
    """
    fields = _source_fields(
        output_dir, product_id=product_id, vertical=vertical, blueprint=blueprint
    )
    for _kind, raw in fields:
        for item in _token_variants(raw):
            spec = _known_spec_for_token(item)
            if spec is not None:
                return spec
    vertical_hint = next((raw for kind, raw in fields if kind == "vertical"), None)
    for _kind, raw in fields:
        derived = _domain_id_from_token(raw)
        if derived:
            return _make_derived_spec(derived, vertical=vertical_hint or derived)
    return _make_derived_spec("general")


def is_finance_domain(
    output_dir: Path | str,
    *,
    product_id: Optional[str] = None,
    vertical: Optional[str] = None,
    blueprint: Any = None,
) -> bool:
    """True for finance_ops / finance-ops product or vertical."""
    spec = detect_domain(
        output_dir, product_id=product_id, vertical=vertical, blueprint=blueprint
    )
    return spec.domain == FINANCE_SPEC.domain


def ensure_coder_brief(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
    blocks_root: Optional[Path] = None,
) -> Path:
    """Ensure ``docs/coder_brief.md`` exists (compile if missing).

    The frozen C-BRIEF is what the CodeWhale (DeepSeek) WRITER reads back as
    its COMPILED C-BRIEF section, so an empty one means the agent authors
    the platform blind.
    """
    root = Path(output_dir)
    dest = root / BRIEF_REL
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    if blueprint is None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            "# coder_brief\n\n"
            "(brief unavailable — no blueprint to compile; open the Floor "
            "session)\n",
            encoding="utf-8",
        )
        return dest
    from app.factory.build.cli_pivot import compose_cbrief

    compiled = compose_cbrief(blueprint, plan=plan, blocks_root=blocks_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(compiled.text, encoding="utf-8")
    return dest
