"""Compile ONE gated coding-agent brief — deterministic fill, no LLM.

TEMPLATE (factory/standards/BRIEF_TEMPLATE.md) is the owner aviation shape.
FILL is registry + block.json + domain pack + intake blueprint. An LLM
never writes or edits brief text.

Staged cuts from the one compiled brief:

    CUT 1 INVENTORY — read-only REUSE / GAPS, then STOP
    CUT 2 VALIDATE  — runner checks claimed ids against the registry
    CUT 3 BUILD     — confirmed gaps, contracts, READS/WRITES/NEVER
    ACCEPTANCE      — harness, not the coder
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import re

from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES
from app.factory.build.intake_blueprint import (
    field_source_index,
    intake_from_product_blueprint,
    validate_intake,
)
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.reuse_lookup import ReuseRecord, resolve_store_presence
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import coder_budget_s
from app.factory.delivery_standard import DOMAIN_PACK_FIELDS
from app.factory.dual_registry import dual_registered_ids
from app.factory.kit_pack import (
    find_kit_manifest,
    kits_for_blocks,
    render_kit_manifest,
)

STANDARDS = Path(__file__).resolve().parents[1] / "standards"
TEMPLATE_PATH = STANDARDS / "BRIEF_TEMPLATE.md"
TEMPLATE_REVISION = "2026-09-04"


class InventoryHalt(RuntimeError):
    """A claimed REUSE is not in the Store registry. Halt before WRITER builds."""


class BriefCompileError(ValueError):
    """The template could not be filled. Unfilled slot, not a default."""


@dataclass
class InventoryItem:
    capability_id: str
    strategy: str
    block_ids: List[str] = field(default_factory=list)
    verified_present: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    notes: str = ""
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    never: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)

    @property
    def is_reuse(self) -> bool:
        return bool(self.verified_present) and not self.missing

    @property
    def is_gap(self) -> bool:
        return not self.block_ids or self.strategy in {"GENERATE", "STUB", "GAP"}


@dataclass
class CompiledBrief:
    """One brief plus the inventory the runner must check before build."""

    text: str
    product_name: str
    vertical: str
    product_id: str
    inventory: List[InventoryItem]
    store_ids: List[str]
    kit_manifests: Dict[str, Any]
    domain_pack: Dict[str, Any]
    missing_reuse: List[str]
    capabilities: List[str]
    intake: Dict[str, Any] = field(default_factory=dict)
    contracts: Dict[str, Any] = field(default_factory=dict)
    reuse_records: Dict[str, Any] = field(default_factory=dict)
    line_sources: Dict[str, str] = field(default_factory=dict)
    budget_s: float = 0.0
    template_revision: str = TEMPLATE_REVISION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_name": self.product_name,
            "vertical": self.vertical,
            "product_id": self.product_id,
            "capabilities": list(self.capabilities),
            "store_ids": list(self.store_ids),
            "missing_reuse": list(self.missing_reuse),
            "inventory": [asdict(item) for item in self.inventory],
            "kit_manifests": dict(self.kit_manifests),
            "domain_pack": dict(self.domain_pack),
            "intake": dict(self.intake),
            "budget_s": self.budget_s,
            "template_revision": self.template_revision,
            "text": self.text,
        }


def load_brief_template() -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    if TEMPLATE_REVISION not in text:
        raise BriefCompileError(
            f"BRIEF_TEMPLATE.md is missing dated revision {TEMPLATE_REVISION}"
        )
    return text


def store_registry_ids(
    blocks_root: Optional[Path] = None,
    factory_shelf: Optional[Path] = None,
) -> Set[str]:
    """Exact block ids the Store / dual registry currently provides."""
    return set(dual_registered_ids(blocks_root, factory_shelf))


def compile_inventory(
    plan: Any,
    store_ids: Iterable[str],
    *,
    reuse_records: Optional[Mapping[str, ReuseRecord]] = None,
) -> List[InventoryItem]:
    """Classify each planned capability as verified REUSE, gap, or missing.

    Never assume a block exists. A claimed id that is not in ``store_ids``
    (or whose REUSE record says present=false) is flagged missing — the
    runner must halt before the build step.
    """
    known = {str(b) for b in store_ids if str(b).strip()}
    records = dict(reuse_records or {})
    items: List[InventoryItem] = []
    for cap in getattr(plan, "capabilities", ()) or ():
        cid = str(getattr(cap, "capability_id", "") or getattr(cap, "id", "") or "")
        strategy = str(getattr(cap, "strategy", "") or "GENERATE").upper()
        claimed = [str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip()]
        present: List[str] = []
        missing: List[str] = []
        reads: List[str] = []
        writes: List[str] = []
        never: List[str] = []
        acceptance: List[str] = []
        for bid in claimed:
            rec = records.get(bid)
            if rec is not None:
                if rec.present:
                    present.append(bid)
                    reads.extend(rec.reads)
                    writes.extend(rec.writes)
                    never.extend(rec.never)
                    acceptance.extend(rec.acceptance)
                else:
                    missing.append(bid)
            elif bid in known:
                present.append(bid)
            else:
                missing.append(bid)
        notes = str(getattr(cap, "notes", "") or "")
        if missing:
            label = "MISSING"
        elif present:
            label = "REUSE" if strategy in {"", "REUSE", "COMPOSE", "ADAPT"} else strategy
        else:
            label = "GAP" if strategy in {"", "REUSE", "COMPOSE"} else strategy
        items.append(
            InventoryItem(
                capability_id=cid,
                strategy=label,
                block_ids=claimed,
                verified_present=present,
                missing=missing,
                notes=notes,
                reads=sorted(set(reads)),
                writes=sorted(set(writes)),
                never=sorted(set(never)),
                acceptance=sorted(set(acceptance)),
            )
        )
    return items


def verify_inventory(compiled: CompiledBrief) -> None:
    """Halt if any inventory row claims REUSE for an id the Store does not have.

    This is CUT 2 — the runner check that must fire BEFORE the WRITER build
    step opens — not at CLONER, and not after the coder has already guessed.
    """
    missing = list(compiled.missing_reuse)
    for item in compiled.inventory:
        missing.extend(f"{item.capability_id}:{bid}" for bid in item.missing)
    seen: Set[str] = set()
    ordered: List[str] = []
    for name in missing:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    if ordered:
        raise InventoryHalt(
            "claimed REUSE is not in the Store registry — HALT before WRITER "
            "build (not at CLONER): " + ", ".join(ordered)
        )


def _capability_description(cap: Any) -> str:
    for attr in ("notes", "description", "customer_words"):
        value = getattr(cap, attr, None)
        if value:
            return str(value)
    data = getattr(cap, "__dict__", {}) or {}
    return str(data.get("description") or data.get("notes") or "")


def _load_kit_manifests(
    block_ids: Sequence[str],
    *,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    grouped = kits_for_blocks(block_ids)
    out: Dict[str, Any] = {}
    for kit_id, bids in grouped.items():
        existing: Optional[Mapping[str, Any]] = None
        src = find_kit_manifest(kit_id, blocks_root)
        if src is not None:
            try:
                existing = json.loads(src.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = None
        out[kit_id] = render_kit_manifest(
            kit_id,
            bids,
            source_kind="brief-compiler",
            existing=existing,
        )
    return out


def synthesize_domain_pack(blueprint: Any, plan: Any) -> Dict[str, Any]:
    """Honest pack from the blueprint. Empty slots stay named, never invented."""
    name = str(getattr(blueprint, "product_name", "") or "platform")
    vertical = str(getattr(blueprint, "vertical", "") or "product")
    summary = str(getattr(blueprint, "summary", "") or "")
    caps = list(getattr(plan, "capabilities", ()) or getattr(blueprint, "capabilities", ()) or [])
    cap_ids = [
        str(getattr(c, "capability_id", "") or getattr(c, "id", "") or "")
        for c in caps
    ]
    cap_ids = [c for c in cap_ids if c]
    users = f"{vertical.replace('_', ' ').replace('-', ' ')} operators"
    pack = {
        "platform_name": name,
        "domain": vertical,
        "product_type": "operations platform",
        "target_users": users,
        "mission": summary or f"A booted {vertical} platform with the listed capabilities.",
        "domain_purpose": summary or f"System of record for {vertical}.",
        "primary_users": [users],
        "required_roles": ["operator", "admin"],
        "required_product_modules": cap_ids,
        "core_business_workflows": [
            f"one-record round-trip for {cid}" for cid in cap_ids
        ] or ["one-record create/read round-trip"],
        "authoritative_calculations": ["none claimed beyond persisted fields"],
        "domain_rules": [
            f"status vocabulary is schema-enforced: {', '.join(ENVELOPE_STATUS_VALUES)}",
            "reserved-keyword fields are refused",
        ],
        "high_impact_actions": ["create", "update", "delete"],
        "prohibited_autonomous_actions": [
            "deploy",
            "store publish",
            "export when the pilot suite is red",
        ],
        "data_sources": ["vendored Store blocks", "local sqlite"],
        "required_connectors": [],
        "required_exports": ["pilot_candidate zip after PRODUCT+STORE green"],
        "security_regulatory_rules": [
            "offline platform — no network, no HTTP store callbacks",
        ],
        "demo_data_requirements": ["one persisted record per capability"],
        "domain_acceptance_conditions": [
            "product boots",
            "own gates green",
            "one-record round-trip per capability",
            "envelope vocab open|in_progress|closed enforced by schema, not prose",
        ],
    }
    return pack


def _section_lines(*parts: str) -> str:
    return "\n".join(part for part in parts if part is not None)


def render_slot_bodies(
    *,
    blueprint: Any,
    plan: Any,
    inventory: Sequence[InventoryItem],
    store_ids: Sequence[str],
    kit_manifests: Mapping[str, Any],
    domain_pack: Mapping[str, Any],
    contracts: Optional[Mapping[str, Any]] = None,
    work_list: Optional[Sequence[str]] = None,
    intake: Optional[Mapping[str, Any]] = None,
    reuse_records: Optional[Mapping[str, Any]] = None,
    budget_s: float = 0.0,
) -> Dict[str, str]:
    """Deterministic slot fill. No LLM. Returns template slot → body."""
    name = str(getattr(blueprint, "product_name", "") or "platform")
    vertical = str(getattr(blueprint, "vertical", "") or "product")
    summary = str(getattr(blueprint, "summary", "") or "")
    packed_intake = dict(intake or {})

    cap_lines: List[str] = []
    for cap in getattr(plan, "capabilities", ()) or ():
        cid = str(getattr(cap, "capability_id", "") or getattr(cap, "id", "") or "")
        desc = _capability_description(cap) or cid
        bids = [str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip()]
        strategy = str(getattr(cap, "strategy", "") or "")
        cap_lines.append(
            f"- {cid} [{strategy or 'planned'}]: {desc}"
            + (f"  blocks={bids}" if bids else "  (no block ids — genuine gap)")
        )

    reuse_lines = []
    gap_lines = []
    missing_lines = []
    for item in inventory:
        if item.missing:
            missing_lines.append(
                f"- {item.capability_id}: claimed {item.block_ids} but NOT in Store: "
                + ", ".join(item.missing)
            )
        if item.verified_present:
            reuse_lines.append(
                f"- {item.capability_id}: REUSE {item.verified_present} "
                "(verified present in Store registry)"
            )
        if item.is_gap or (not item.verified_present and not item.missing):
            gap_lines.append(
                f"- {item.capability_id}: GAP — author this logic "
                f"({item.notes or item.strategy or 'no verified block'})"
            )

    users = (packed_intake.get("users") or {}).get("value") or domain_pack.get("primary_users") or []
    roles = (packed_intake.get("roles") or {}).get("value") or domain_pack.get("required_roles") or []
    done_when = (packed_intake.get("done_when") or {}).get("value") or []

    target = _section_lines(
        f"Booted {vertical} platform: {name}.",
        summary,
        "",
        "Who it is for: " + ", ".join(str(u) for u in users),
        "Roles: " + ", ".join(str(r) for r in roles),
        "",
        "Capabilities:",
        "\n".join(cap_lines) or "- (none)",
    )

    inventory_body = _section_lines(
        "Coder: list what the Store already provides. REUSE by exact block id, "
        "verified present — flag missing, never assume.",
        "",
        "Store registry (exact ids, verified): " + ", ".join(sorted(store_ids)),
        "",
        "REUSE (verified present):",
        "\n".join(reuse_lines) or "- (none)",
        "",
        "GAPS (you author; do not invent a block id):",
        "\n".join(gap_lines) or "- (none)",
        "",
        "MISSING claimed REUSE (runner HALTS here if any):",
        "\n".join(missing_lines) or "- (none)",
        "",
        "CUT 1 is read-only. Stop after this inventory. Do not build yet.",
    )

    validate_body = _section_lines(
        "CUT 2 — runner validates ids against the registry (not the coder).",
        "Claimed REUSE that is not present HALTS before WRITER build, not at CLONER.",
        "Verified present: "
        + ", ".join(
            bid
            for item in inventory
            for bid in item.verified_present
        )
        or "(none)",
        "Missing: "
        + ", ".join(bid for item in inventory for bid in item.missing)
        or "(none)",
    )

    do_lines = [
        "Build only confirmed gaps. Do not re-implement a verified REUSE block.",
        "Invocation contracts: pass action= as a keyword, never inside the payload dict.",
        "Prefer action=BLOCK_DEFAULT_ACTIONS.get(block_id).",
        "Call execute() for EVERY id in BLOCK_IDS.",
        "If you assign a block, you feed it — construct block inputs; do not demand "
        "block-specific keys (topic, sql/table, file paths, team_id, channel, steps) "
        "from the caller.",
        "Declare vocabularies on the spec (schema), not in prose.",
        f"Envelope status vocabulary (schema-enforced): {' | '.join(ENVELOPE_STATUS_VALUES)}.",
        "Three tests per block are already owned by the harness (TESTER is not an LLM role).",
        "Scope READS / WRITES / NEVER explicitly in each handler you author.",
        f"Budget wall: {int(budget_s)}s (FACTORY_CODER_BUDGET_S / staged wall).",
    ]
    for item in inventory:
        if item.reads or item.writes or item.never:
            do_lines.append(
                f"- {item.capability_id} READS={item.reads or ['(none)']} "
                f"WRITES={item.writes or ['(none)']} NEVER={item.never or ['(none)']}"
            )
        if item.acceptance:
            do_lines.append(
                f"- {item.capability_id} block acceptance: {item.acceptance}"
            )
    findings = [str(item) for item in (work_list or []) if str(item).strip()]
    if findings:
        do_lines += [
            "",
            "A previous attempt failed these checks — keep what works and fix only these:",
            *[f"- {item}" for item in findings],
        ]
    do_lines += [
        "",
        "Kit manifests (Factory shelf + on-disk packs):",
        json.dumps(kit_manifests, indent=2, sort_keys=True),
    ]
    if contracts:
        do_lines += [
            "",
            "Block contracts (invoke only actions they support):",
            json.dumps(dict(contracts), indent=2, sort_keys=True),
        ]
    if reuse_records:
        scoped = {
            bid: rec if isinstance(rec, dict) else rec.to_dict()
            for bid, rec in dict(reuse_records).items()
        }
        do_lines += [
            "",
            "REUSE records (present/reuse + reads/writes/never/acceptance):",
            json.dumps(scoped, indent=2, sort_keys=True),
        ]
    if domain_pack:
        packed = {
            key: domain_pack.get(key)
            for key in ("mission", "domain_purpose", "domain_acceptance_conditions")
            + DOMAIN_PACK_FIELDS
            if domain_pack.get(key) not in (None, "", [])
        }
        if packed:
            do_lines += ["", "Domain pack (binding fields):", json.dumps(packed, indent=2, sort_keys=True)]
    if done_when:
        do_lines += ["", "Done when (from intake blueprint):", *[f"- {item}" for item in done_when]]

    acceptance = _section_lines(
        "Fails loud. The run is not done until ALL of these are true. "
        "ACCEPTANCE is run by the harness, not the coder.",
        "- the product boots  [check:boot]",
        "- own gates green  [check:gates]",
        "- one-record round-trip per capability (POST creates, GET returns it)  [check:round_trip]",
        "- the domain pack's domain_acceptance_conditions hold  [check:domain_acceptance]",
        f"- envelope vocab {', '.join(ENVELOPE_STATUS_VALUES)} enforced by schema, not prose  [check:envelope_schema]",
        f"- PRODUCT gate: {GATE_SCOPES['PRODUCT']}  [check:product_gate]",
        f"- STORE gate: {GATE_SCOPES['STORE']}  [check:store_gate]",
        "- ledger records pilot_ready=true  [check:ledger]",
        "",
        "The harness's acceptance IS the tester. Do not write decorative tests. "
        "Do not treat thin SUCCESS / templates-only / stubbed capabilities as done.",
    )

    forbidden = _section_lines(
        "- thin SUCCESS (code-cycle green, pilot_ready=false)",
        "- decorative tests",
        "- reserved-keyword fields (action inside the payload dict, id as a domain field)",
        "- unlisted blocks (ids not in the Store registry / inventory)",
        "- assuming a REUSE id is present when STEP 0 flagged it missing",
        "- one handle() / one spec / one route at a time — this brief is the whole job",
        "- weakening honesty or exporting when the pilot suite is red",
    )

    return {
        "TARGET": target,
        "INVENTORY": inventory_body,
        "VALIDATE": validate_body,
        "BUILD": "\n".join(do_lines),
        "ACCEPTANCE": acceptance,
        "FORBIDDEN": forbidden,
    }


def fill_template(template: str, slots: Mapping[str, str]) -> str:
    """Replace {{SLOT}} markers. Leftover markers are a compile error, not a default."""
    text = template
    for key, body in slots.items():
        marker = "{{" + key + "}}"
        if marker not in text:
            raise BriefCompileError(f"BRIEF_TEMPLATE.md is missing slot {marker}")
        text = text.replace(marker, body.strip() + "\n")
    leftovers = [m.group(0) for m in re.finditer(r"\{\{[A-Z0-9_]+\}\}", text)]
    if leftovers:
        raise BriefCompileError(
            "unfilled template slot (lint failure, not a default): "
            + ", ".join(sorted(set(leftovers)))
        )
    return text if text.endswith("\n") else text + "\n"


def _line_sources_for(
    slots: Mapping[str, str],
    *,
    intake: Mapping[str, Any],
    domain_pack: Mapping[str, Any],
    inventory: Sequence[InventoryItem],
    store_ids: Sequence[str],
) -> Dict[str, str]:
    sources = field_source_index(intake)
    for key, value in domain_pack.items():
        if isinstance(value, str) and value.strip():
            sources[value.strip()[:80]] = f"domain_pack.{key}"
        elif isinstance(value, list):
            for item in value:
                if str(item).strip():
                    sources[str(item).strip()[:80]] = f"domain_pack.{key}"
    for bid in store_ids:
        sources[str(bid)] = f"manifest.store.{bid}"
    for item in inventory:
        if item.capability_id:
            sources[item.capability_id] = f"blueprint.capabilities.{item.capability_id}"
        for bid in item.block_ids + item.verified_present:
            sources[bid] = f"manifest.block.{bid}"
    for key, body in slots.items():
        sources[f"slot:{key}"] = f"template.{key}"
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                sources[stripped[:80]] = f"template.{key}"
    sources[CODING_AGENT_BRIEF[:80]] = "standard.CODING_AGENT_BRIEF"
    for line in CODING_AGENT_BRIEF.splitlines():
        stripped = line.strip()
        if stripped:
            sources[stripped[:80]] = "standard.CODING_AGENT_BRIEF"
    return sources


def render_gated_brief(
    *,
    blueprint: Any,
    plan: Any,
    inventory: Sequence[InventoryItem],
    store_ids: Sequence[str],
    kit_manifests: Mapping[str, Any],
    domain_pack: Mapping[str, Any],
    contracts: Optional[Mapping[str, Any]] = None,
    work_list: Optional[Sequence[str]] = None,
    intake: Optional[Mapping[str, Any]] = None,
    reuse_records: Optional[Mapping[str, Any]] = None,
    budget_s: float = 0.0,
) -> str:
    """Fill BRIEF_TEMPLATE.md. LLM never writes this text."""
    slots = render_slot_bodies(
        blueprint=blueprint,
        plan=plan,
        inventory=inventory,
        store_ids=store_ids,
        kit_manifests=kit_manifests,
        domain_pack=domain_pack,
        contracts=contracts,
        work_list=work_list,
        intake=intake,
        reuse_records=reuse_records,
        budget_s=budget_s,
    )
    filled = fill_template(load_brief_template(), slots)
    return CODING_AGENT_BRIEF + "\n\n" + filled


def compile_brief(
    blueprint: Any,
    plan: Any,
    *,
    blocks_root: Optional[Path] = None,
    factory_shelf: Optional[Path] = None,
    store_ids: Optional[Iterable[str]] = None,
    domain_pack: Optional[Mapping[str, Any]] = None,
    contracts: Optional[Mapping[str, Any]] = None,
    work_list: Optional[Sequence[str]] = None,
    intake: Optional[Mapping[str, Any]] = None,
    chat_turns: Optional[Sequence[Mapping[str, Any]]] = None,
    brief: str = "",
    budget_s: Optional[float] = None,
    reuse_http_get=None,
) -> CompiledBrief:
    """Compile one brief from the intake blueprint, domain pack, and Store registry."""
    known = set(store_ids) if store_ids is not None else store_registry_ids(
        blocks_root, factory_shelf
    )
    claimed = []
    for cap in getattr(plan, "capabilities", ()) or ():
        claimed.extend(str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip())
    # Explicit store_ids (tests / offline compile) skip live HTTP unless
    # the caller passed a getter. Floor/WRITER leave store_ids unset so
    # STEP 0 feature-detects Blocks #106.
    skip_http = store_ids is not None and reuse_http_get is None
    records = resolve_store_presence(
        claimed,
        local_ids=known,
        http_get=(lambda *_a, **_k: None) if skip_http else reuse_http_get,
    )
    # HTTP present=true counts as verified even if the local shelf lagged.
    for bid, rec in records.items():
        if rec.present:
            known.add(bid)
    inventory = compile_inventory(plan, known, reuse_records=records)
    missing = [bid for item in inventory for bid in item.missing]
    kits = _load_kit_manifests(claimed, blocks_root=blocks_root)
    pack = dict(domain_pack) if domain_pack else synthesize_domain_pack(blueprint, plan)
    packed_intake = dict(intake) if intake else intake_from_product_blueprint(
        blueprint,
        plan=plan,
        chat_turns=chat_turns,
        brief=brief,
        domain_pack=pack,
    )
    validate_intake(packed_intake)
    wall = float(budget_s) if budget_s is not None else float(coder_budget_s())
    slots = render_slot_bodies(
        blueprint=blueprint,
        plan=plan,
        inventory=inventory,
        store_ids=sorted(known),
        kit_manifests=kits,
        domain_pack=pack,
        contracts=contracts,
        work_list=work_list,
        intake=packed_intake,
        reuse_records=records,
        budget_s=wall,
    )
    text = CODING_AGENT_BRIEF + "\n\n" + fill_template(load_brief_template(), slots)
    line_sources = _line_sources_for(
        slots,
        intake=packed_intake,
        domain_pack=pack,
        inventory=inventory,
        store_ids=sorted(known),
    )
    return CompiledBrief(
        text=text,
        product_name=str(getattr(blueprint, "product_name", "") or "platform"),
        vertical=str(getattr(blueprint, "vertical", "") or "product"),
        product_id=str(getattr(blueprint, "product_id", "") or ""),
        inventory=inventory,
        store_ids=sorted(known),
        kit_manifests=kits,
        domain_pack=pack,
        missing_reuse=missing,
        capabilities=[item.capability_id for item in inventory],
        intake=packed_intake,
        contracts=dict(contracts or {}),
        reuse_records={bid: rec.to_dict() for bid, rec in records.items()},
        line_sources=line_sources,
        budget_s=wall,
    )


def compile_brief_from_ctx(ctx: Any) -> CompiledBrief:
    """RoleContext → compiled brief. Store ids from dual registry + REUSE HTTP."""
    contracts: Dict[str, Any] = {}
    vendored = [b for b in (getattr(ctx, "state", {}) or {}).get("vendored_blocks", ()) if b]
    if vendored and hasattr(ctx, "workspace"):
        try:
            from app.factory.build.roles_handlers import _block_contract

            contracts = {b: _block_contract(ctx, b) for b in vendored}
        except Exception:  # noqa: BLE001 — brief must still compile without contracts
            contracts = {}
    intake = (getattr(ctx, "state", {}) or {}).get("intake_blueprint")
    turns = (getattr(ctx, "state", {}) or {}).get("chat_turns")
    # The brief names the configured wall, not remaining seconds — leftover
    # time is non-deterministic and would make two compiles disagree.
    return compile_brief(
        ctx.blueprint,
        ctx.plan,
        blocks_root=getattr(ctx, "blocks_root", None),
        contracts=contracts or None,
        work_list=list(getattr(ctx, "work_list", ()) or ()),
        intake=intake if isinstance(intake, dict) else None,
        chat_turns=turns if isinstance(turns, list) else None,
    )


def brief_fingerprint(compiled: CompiledBrief) -> Dict[str, Any]:
    """Identity the lettings golden must reproduce after a template revision."""
    return {
        "template_revision": compiled.template_revision,
        "capabilities": list(compiled.capabilities),
        "inventory": [
            {
                "capability_id": item.capability_id,
                "strategy": item.strategy,
                "block_ids": list(item.block_ids),
                "verified_present": list(item.verified_present),
                "missing": list(item.missing),
            }
            for item in compiled.inventory
        ],
        "missing_reuse": list(compiled.missing_reuse),
        "budget_s": compiled.budget_s,
    }
