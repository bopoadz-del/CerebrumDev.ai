"""BRIEF LINT — reject before FACTORY_CODE_CLI session opens.

A brief that fails lint never reaches the coder. Mutation tests plant a
broken brief and a planted unsourced line; both must be refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from app.factory.build.persist_accept import persist_accept_needles
from app.factory.build.workflow_accept import (
    declares_event_bus_workflow,
    workflow_accept_needles,
)

SLOT_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
HEADING_RE = re.compile(r"^(=+|CUT \d|TARGET|STEP 0|DO\b|ACCEPTANCE|FORBIDDEN|# )")
BUDGET_RE = re.compile(r"\b(?:budget|wall)[^\n]{0,40}?(\d+)\s*s\b", re.I)

#: Acceptance bullets the harness actually runs. A line without one of these
#: needles is an acceptance line without an executable check.
EXECUTABLE_ACCEPTANCE = (
    ("product boots", "boot"),
    ("own gates green", "gates"),
    ("one-record round-trip", "round_trip"),
    ("round-trip per capability", "round_trip"),
    ("did not remember a record", "round_trip"),
    ("no such table", "round_trip"),
    ("store.save(ENTITY, payload)", "round_trip"),
    ("factory-grounded persist", "round_trip"),
    ("alembic entity", "round_trip"),
    ("writer_behaviour", "writer_behaviour"),
    ("accepted its own schema", "writer_behaviour"),
    ("own FIELDS/CONSTRAINTS", "writer_behaviour"),
    ("schema-accept", "writer_behaviour"),
    ("event_bus_workflow", "event_bus_workflow"),
    ("event_bus workflow", "event_bus_workflow"),
    ("test_every_capability_route_accepts_payload", "event_bus_workflow"),
    ("step_N (event_bus)", "event_bus_workflow"),
    ("step_0 (event_bus)", "event_bus_workflow"),
    ("step_1 (event_bus)", "event_bus_workflow"),
    ("step_2 (event_bus)", "event_bus_workflow"),
    ("appointment_scheduling", "event_bus_workflow"),
    ("appointment_booking", "event_bus_workflow"),
    ("automated_reminders", "event_bus_workflow"),
    ("every event_bus", "event_bus_workflow"),
    ("never the raw schema sample", "event_bus_workflow"),
    ("prepared contract", "event_bus_workflow"),
    ("action=publish", "event_bus_workflow"),
    ("payload dict", "event_bus_workflow"),
    ("input.topic", "event_bus_workflow"),
    ("input.message", "event_bus_workflow"),
    ("factory-grounded", "event_bus_workflow"),
    ('execute("workflow", payload)', "event_bus_workflow"),
    ("input.tool", "event_bus_workflow"),
    ("domain_acceptance_conditions", "domain_acceptance"),
    ("domain pack", "domain_acceptance"),
    ("envelope vocab", "envelope_schema"),
    ("open|in_progress|closed", "envelope_schema"),
    ("open, in_progress, closed", "envelope_schema"),
    ("product gate", "product_gate"),
    ("store gate", "store_gate"),
    ("pilot_ready", "ledger"),
    ("ledger records", "ledger"),
    ("harness", "harness"),
)

#: C-BRIEF packaging contract. Dropping these lets Kimi rewrite
#: ``app/actions/__init__.py`` with eager ``from app.actions import``
#: re-exports (live VetCare: circular pet_records_management).
ACTIONS_PACKAGING_NEEDLES = (
    "from app.actions import",
    "workspace does not import",
    "app.routes",
    "app.main from a",
)


TEMPLATE_STATIC_NEEDLES = (
    "c-brief template",
    "revision:",
    "owner_shape:",
    "fill:",
    "llm_writes_brief:",
    "changes to this file",
    "reproduced",
    "coder: list what",
    "store registry",
    "reuse (verified",
    "gaps (you author",
    "missing claimed",
    "build only confirmed",
    "invocation contracts",
    "prefer action=",
    "call execute()",
    "from app.actions import",
    "workspace does not import",
    "eager re-export",
    "app.routes",
    "if you assign a block",
    "declare vocabularies",
    "envelope status vocabulary",
    "three tests per block",
    "scope reads",
    "kit manifests",
    "block contracts",
    "domain pack",
    "fails loud",
    "the run is not done",
    "the product boots",
    "own gates green",
    "one-record round-trip",
    "did not remember a record",
    "no such table",
    "store.save(entity, payload)",
    "factory-grounded persist",
    "alembic entity",
    "0001_baseline",
    "writer_behaviour",
    "accepted its own schema",
    "own fields/constraints",
    "schema-accept",
    "event_bus_workflow",
    "event_bus workflow",
    "test_every_capability_route_accepts_payload",
    "step_n (event_bus)",
    "step_0 (event_bus)",
    "step_1 (event_bus)",
    "step_2 (event_bus)",
    "appointment_scheduling",
    "appointment_booking",
    "automated_reminders",
    "every event_bus",
    "never the raw schema sample",
    "schema sample refused",
    "accept-payload",
    "action=publish",
    "payload dict",
    "input.topic",
    "input.message",
    "'input': payload",
    "prepared event_bus",
    "factory-grounded",
    'execute("workflow", payload)',
    "execute(block_id, payload)",
    "input.tool",
    '"channel": "mcp"',
    "the domain pack",
    "envelope vocab",
    "product gate:",
    "store gate:",
    "ledger records",
    "the harness",
    "thin success",
    "decorative tests",
    "reserved-keyword",
    "unlisted blocks",
    "assuming a reuse",
    "one handle()",
    "weakening honesty",
    "cut 1",
    "cut 2",
    "cut 3",
    "step 0",
    "runner validates",
    "factory coding-agent brief",
    "you are manufacturing",
    "exit condition",
    "three gates",
    "code →",
    "product →",
    "store →",
    "contracts you must honour",
    "this brief is the horizon",
    "budget",
    "approve",
    "halt before writer",
    "verified present",
    "genuine gap",
    "no verified block",
    "not in store",
    "schema-enforced",
    "offline platform",
    "do not invent scopes",
    "not declared on block.json",
    "pre-flip",
    "unverified reuse dropped",
    "exact-id present=false",
    "store exact-id",
    "block scopes",
    "block-level acceptance",
    "report-only",
    "l2.2",
    "inventing reads/writes/never/acceptance",
)


class BriefLintError(ValueError):
    """The compiled brief is rejected. The coder session must not open."""


@dataclass
class BriefLintResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    checks: Dict[str, Any] = field(default_factory=dict)

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise BriefLintError(
                "BRIEF_LINT_REJECTED — session never opens: " + "; ".join(self.errors)
            )

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), "checks": dict(self.checks)}


def _content_lines(text: str) -> List[str]:
    lines: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if set(line) <= {"=", "-", " "}:
            continue
        if HEADING_RE.match(line):
            continue
        lines.append(line)
    return lines


def _acceptance_section(text: str) -> str:
    """Only the ACCEPTANCE cut, not the word inside CODING_AGENT_BRIEF."""
    marker = "ACCEPTANCE (harness"
    start = text.find(marker)
    if start < 0:
        start = text.rfind("\nACCEPTANCE\n")
        if start < 0:
            start = text.rfind("\nACCEPTANCE ")
    if start < 0:
        return ""
    rest = text[start:]
    end = rest.find("\nFORBIDDEN")
    if end < 0:
        end = rest.find("FORBIDDEN")
    return rest if end < 0 else rest[:end]


def _acceptance_bullets(text: str) -> List[str]:
    section = _acceptance_section(text)
    return [
        line.strip()
        for line in section.splitlines()
        if line.strip().startswith("-")
    ]


def _has_executable_check(bullet: str) -> bool:
    blob = bullet.lower()
    if "[check:" in blob:
        return True
    return any(needle in blob for needle, _name in EXECUTABLE_ACCEPTANCE)


def _line_is_sourced(
    line: str,
    *,
    line_sources: Mapping[str, str],
    source_needles: Iterable[str],
) -> bool:
    lowered = line.lower()
    if any(needle in lowered for needle in source_needles):
        return True
    if any(needle and needle.lower() in lowered for needle in line_sources):
        return True
    if any(value and str(value).lower() in lowered for value in line_sources.values()):
        return True
    if any(static in lowered for static in TEMPLATE_STATIC_NEEDLES):
        return True
    return False


def lint_brief(
    compiled: Any,
    *,
    line_sources: Optional[Mapping[str, str]] = None,
    extra_sources: Optional[Sequence[str]] = None,
) -> BriefLintResult:
    """Reject a brief that is not ready to dispatch."""
    errors: List[str] = []
    text = compiled.text if hasattr(compiled, "text") else str(compiled)
    sources = dict(line_sources or {})
    if hasattr(compiled, "line_sources") and compiled.line_sources:
        sources.update(compiled.line_sources)
    extra = list(extra_sources or [])
    extra.extend(str(s) for s in (getattr(compiled, "source_needles", ()) or ()))

    slots = SLOT_RE.findall(text)
    if slots:
        errors.append("unfilled template slot: " + ", ".join(sorted(set(slots))))

    missing = list(getattr(compiled, "missing_reuse", ()) or [])
    for item in getattr(compiled, "inventory", ()) or []:
        missing.extend(getattr(item, "missing", ()) or [])
    if missing:
        errors.append(
            "unresolved block id: " + ", ".join(sorted({str(m) for m in missing}))
        )

    contracts = getattr(compiled, "contracts", None) or {}
    manifests = getattr(compiled, "kit_manifests", None) or {}
    manifest_fields: Set[str] = set()
    for kit in manifests.values() if isinstance(manifests, dict) else []:
        if not isinstance(kit, dict):
            continue
        for key in ("blocks", "inputs", "fields", "reads", "writes"):
            values = kit.get(key)
            if isinstance(values, list):
                for entry in values:
                    if isinstance(entry, dict):
                        name = entry.get("id") or entry.get("name")
                        if name:
                            manifest_fields.add(str(name))
                    elif entry:
                        manifest_fields.add(str(entry))
            elif isinstance(values, dict):
                manifest_fields.update(str(k) for k in values)
    orphan_fields: List[str] = []
    if isinstance(contracts, dict):
        for bid, contract in contracts.items():
            if not isinstance(contract, dict):
                continue
            for field in contract.get("declared_inputs") or []:
                name = field.get("name") if isinstance(field, dict) else field
                if not name:
                    continue
                in_manifest = (
                    str(name) in manifest_fields
                    or str(bid) in manifest_fields
                    or bool(contract.get("block_id"))
                )
                # A harvested block.json contract is itself a manifest entry.
                if contract.get("from_block_json") or contract.get("declared_inputs"):
                    in_manifest = True
                if not in_manifest:
                    orphan_fields.append(f"{bid}.{name}")
    if orphan_fields:
        errors.append(
            "contract field without manifest: " + ", ".join(sorted(set(orphan_fields)))
        )

    bullets = _acceptance_bullets(text)
    unchecked = [b for b in bullets if not _has_executable_check(b)]
    if not bullets:
        errors.append("acceptance line without executable check: (none written)")
    elif unchecked:
        errors.append(
            "acceptance line without executable check: "
            + "; ".join(unchecked[:5])
        )
    acceptance = _acceptance_section(text).lower()
    if "writer_behaviour" not in acceptance and "own fields/constraints" not in acceptance:
        errors.append(
            "acceptance missing writer_behaviour schema-accept "
            "(no capability accepted its own schema)"
        )
    persist_needles = persist_accept_needles()
    missing_persist = [
        needle
        for needle in persist_needles
        if needle.lower() not in text.lower()
    ]
    if missing_persist:
        errors.append(
            "brief dropped PRODUCT one-record persist contract "
            "(alembic entity / store.save): "
            + ", ".join(missing_persist[:4])
        )

    blob = text.lower()
    missing_packaging = [
        needle
        for needle in ACTIONS_PACKAGING_NEEDLES
        if needle.lower() not in blob
    ]
    if missing_packaging:
        errors.append(
            "brief dropped actions packaging contract "
            "(circular app.actions import): "
            + ", ".join(missing_packaging[:4])
        )

    if declares_event_bus_workflow(compiled):
        blob = text.lower()
        missing_needles = [
            needle
            for needle in workflow_accept_needles()
            if needle.lower() not in blob
        ]
        if missing_needles:
            errors.append(
                "brief dropped event_bus / accept-payload workflow contract "
                "(capability declares workflow + event_bus): "
                + ", ".join(missing_needles[:4])
            )

    budget_s = getattr(compiled, "budget_s", None)
    if budget_s in (None, "", 0, 0.0) and not BUDGET_RE.search(text):
        errors.append("missing budget")
    elif budget_s in (None, "", 0, 0.0):
        # Text mentions a budget but the compiled object forgot the number.
        errors.append("missing budget")

    unsourced: List[str] = []
    for line in _content_lines(text):
        if _line_is_sourced(line, line_sources=sources, source_needles=extra):
            continue
        # Short structural leftovers (bars already skipped).
        if len(line) < 8:
            continue
        unsourced.append(line)
    if unsourced:
        errors.append(
            "orphan line (no blueprint / domain-pack / manifest source): "
            + unsourced[0][:160]
        )

    return BriefLintResult(
        ok=not errors,
        errors=errors,
        checks={
            "slots": slots,
            "missing_reuse": sorted({str(m) for m in missing}),
            "acceptance_bullets": len(bullets),
            "budget_s": budget_s,
            "unsourced": unsourced[:8],
        },
    )


def lint_or_raise(compiled: Any, **kwargs: Any) -> BriefLintResult:
    result = lint_brief(compiled, **kwargs)
    result.raise_if_failed()
    return result
