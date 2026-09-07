"""Fail-closed authorship accounting — CLI keep-path is agent-written.

sess_4e1ec7afa3894dc8 on tip 160af0e: harvest kept four FACTORY_CODE_CLI
handlers tagged ``coder CLI (/usr/local/bin/kimi)``, but writer_contract
and Floor authorship only counted ``coder LLM`` prefixes. The operator
saw ``0 by the coding agent, 27 templated`` after a ~16-minute CLI keep.

A capability kept from CLI is coding-agent work. It must not also sit in
the templated inventory for the same run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = (
    "DualListedAuthorshipError",
    "FULL_PILOT_AUTHORSHIP_CHECK",
    "FULL_PILOT_MIN_AUTHORED_ACTIONS",
    "FullPilotAuthorship",
    "coding_agent_artifact_ids",
    "dual_listed_capability_ids",
    "exclusive_authorship_caps",
    "cli_authored_ids_from",
    "full_pilot_authorship_acceptance_line",
    "full_pilot_authorship_forbidden_lines",
    "full_pilot_authorship_from",
    "full_pilot_authorship_needles",
    "full_pilot_authorship_rules_text",
    "is_action_artifact_id",
    "thin_store_green_export_blocker",
    "is_coding_agent_source",
    "kept_handler_ids_from",
    "promote_cli_keep_ids",
    "refuse_dual_listed_caps",
    "writer_authorship_counts",
    "writer_contract_role_detail",
)

#: Launching-ready full-pilot bar. STORE_GREEN / package zip is not honest
#: below this many agent-written action handlers (or cli_authored_ids).
FULL_PILOT_MIN_AUTHORED_ACTIONS = 5
FULL_PILOT_AUTHORSHIP_CHECK = "full_pilot_authorship"


def full_pilot_authorship_rules_text() -> str:
    """BUILD cut: coder must emit the launching-ready floor before done."""
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    return (
        f"Launching-ready full-pilot authorship floor: emit ≥{n} keepable "
        "agent-written app/actions/*.py handlers (or equivalent "
        f"cli_authored_ids). Fewer than {n} is FACTORY_CODE_CLI_THIN_AUTHORSHIP "
        "— CODE_GREEN / pilot_ready=false, package 409. Do not treat the job "
        "as done below this floor."
    )


def full_pilot_authorship_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    return (
        f"- launching-ready full-pilot authorship: ≥{n} keepable "
        "agent-written app/actions/*.py handlers (or equivalent "
        f"cli_authored_ids)  [check:{FULL_PILOT_AUTHORSHIP_CHECK}]"
    )


def full_pilot_authorship_forbidden_lines() -> str:
    """FORBIDDEN cut: the #387 thin-authorship refuse the coder must see."""
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    return (
        f"- authorship below the launching-ready full-pilot floor "
        f"(<{n} agent-written app/actions/*.py / cli_authored_ids) — "
        "FACTORY_CODE_CLI_THIN_AUTHORSHIP"
    )


def full_pilot_authorship_needles() -> Sequence[str]:
    """Needles lint requires on every compiled brief."""
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    return (
        f"≥{n}",
        "full-pilot authorship",
        "app/actions/*.py",
        "cli_authored_ids",
        "FACTORY_CODE_CLI_THIN_AUTHORSHIP",
        f"[check:{FULL_PILOT_AUTHORSHIP_CHECK}]",
    )

#: Writer extras that are not ``app/actions/*.py`` handlers.
_NON_ACTION_ARTIFACT_IDS = frozenset(
    {
        "jobs",
        "readme",
        "entrypoint",
        "requirements",
        "release_gate",
        "deploy_scaffold",
        "network_posture",
        "sbom",
        "permissions",
        "domain_pack",
        "persistence",
        "migrations",
        "deploy_observe",
        "domain_acceptance",
        "emitter_parity",
    }
)


class DualListedAuthorshipError(ValueError):
    """A capability cannot be both agent-written and templated."""


def is_coding_agent_source(source: Any) -> bool:
    """True for coder LLM, coder CLI, or harvested keep-path labels.

    Factory-grounded persist/event_bus emit is not the coding agent.
    Deterministic templates are not the coding agent.
    """
    text = str(source or "").strip()
    if not text:
        return False
    if text.startswith("coder LLM") or text.startswith("coder CLI"):
        return True
    if text.startswith("FACTORY_CODE_CLI"):
        return True
    lowered = text.lower()
    return lowered in {"harvested workspace handler", "compiled-brief oneshot"}


def coding_agent_artifact_ids(sources: Optional[Mapping[str, Any]]) -> List[str]:
    return sorted(
        str(k) for k, v in (sources or {}).items() if is_coding_agent_source(v)
    )


def writer_authorship_counts(
    sources: Optional[Mapping[str, Any]],
) -> Dict[str, int]:
    src = dict(sources or {})
    written = len(coding_agent_artifact_ids(src))
    return {
        "artifacts": len(src),
        "agent_written": written,
        "templated": len(src) - written,
    }


def writer_contract_role_detail(
    capabilities: Sequence[str],
    sources: Optional[Mapping[str, Any]],
) -> str:
    counts = writer_authorship_counts(sources)
    return (
        f"{len(capabilities)} capability(ies); {counts['artifacts']} artifact(s) — "
        f"{counts['agent_written']} by the coding agent, "
        f"{counts['templated']} templated"
    )


def _dispatch_map(state_or_dispatch: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    raw = dict(state_or_dispatch or {})
    nested = raw.get("brief_dispatch")
    if isinstance(nested, Mapping):
        return nested
    return raw


def kept_handler_ids_from(state_or_dispatch: Optional[Mapping[str, Any]]) -> List[str]:
    """``brief_dispatch.kept_handler_ids`` from state or the dispatch map itself."""
    dispatch = _dispatch_map(state_or_dispatch)
    ids: List[str] = []
    for item in dispatch.get("kept_handler_ids") or ():
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def cli_authored_ids_from(state_or_dispatch: Optional[Mapping[str, Any]]) -> Optional[List[str]]:
    """Explicit CLI harvest ids, or None when the field was never recorded.

    An empty list after kimi/DeepSeek exit 0 is not a keep-path credit
    (``FACTORY_CODE_CLI_NO_AUTHORSHIP``). Missing key keeps #375
    ``kept_handler_ids`` promotion for real CLI keep-path receipts.
    """
    dispatch = _dispatch_map(state_or_dispatch)
    if "cli_authored_ids" not in dispatch:
        return None
    ids: List[str] = []
    for item in dispatch.get("cli_authored_ids") or ():
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def promote_cli_keep_ids(state_or_dispatch: Optional[Mapping[str, Any]]) -> List[str]:
    """Ids inspect may credit as CLI keep-path writes.

    Prefer ``cli_authored_ids`` when present (including empty). Otherwise
    fall back to ``kept_handler_ids`` for receipts that predate that field.
    """
    authored = cli_authored_ids_from(state_or_dispatch)
    if authored is not None:
        return authored
    return kept_handler_ids_from(state_or_dispatch)


def exclusive_authorship_caps(
    caps_written: Sequence[str],
    caps_templated: Sequence[str],
) -> Tuple[List[str], List[str]]:
    """Written wins. The same capability id must not appear in both lists."""
    written = [
        c
        for c in dict.fromkeys(str(x).strip() for x in caps_written)
        if c
    ]
    written_set = set(written)
    templated = [
        c
        for c in dict.fromkeys(str(x).strip() for x in caps_templated)
        if c and c not in written_set
    ]
    return written, templated


def dual_listed_capability_ids(
    caps_written: Iterable[str],
    caps_templated: Iterable[str],
) -> List[str]:
    return sorted({str(c) for c in caps_written} & {str(c) for c in caps_templated})


def refuse_dual_listed_caps(
    caps_written: Sequence[str],
    caps_templated: Sequence[str],
) -> None:
    """Fail-closed: overlapping written+templated ids are a factory bug."""
    overlap = dual_listed_capability_ids(caps_written, caps_templated)
    if overlap:
        raise DualListedAuthorshipError(
            "capability id(s) listed as both agent-written and templated: "
            + ", ".join(overlap)
        )


def is_action_artifact_id(artifact_id: str) -> bool:
    """True for a capability action-handler id, not models/routes/extras."""
    text = str(artifact_id or "").strip()
    if not text or ":" in text or "/" in text:
        return False
    if text.endswith(".py") or text.endswith(".tsx") or text.endswith(".md"):
        return False
    if text in _NON_ACTION_ARTIFACT_IDS:
        return False
    if text.startswith("template_"):
        return False
    return True


def _as_nonneg_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _unique_ids(values: Iterable[Any]) -> List[str]:
    ids: List[str] = []
    for item in values:
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def _cli_ids_from_mapping(blob: Any) -> Optional[List[str]]:
    if not isinstance(blob, Mapping):
        return None
    if "cli_authored_ids" in blob:
        return _unique_ids(blob.get("cli_authored_ids") or ())
    nested = cli_authored_ids_from(blob)
    return nested


@dataclass(frozen=True)
class FullPilotAuthorship:
    """Measured action-handler authorship against the launching-ready floor."""

    action_ids: List[str] = field(default_factory=list)
    cli_authored_ids: List[str] = field(default_factory=list)
    action_py: int = 0
    measured: bool = False
    meets_floor: bool = False

    @property
    def below_floor(self) -> bool:
        return self.measured and not self.meets_floor


def full_pilot_authorship_from(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
) -> FullPilotAuthorship:
    """Count agent-written action handlers / ``cli_authored_ids``.

    ``full_pilot`` needs ≥ ``FULL_PILOT_MIN_AUTHORED_ACTIONS`` of either.
    Missing counts are not a pass. A present count below the floor is a
    measured refuse (VetCare action_py=3, lettings action_py=4).
    """
    status = dict(status or {})
    authorship = status.get("authorship")
    if not isinstance(authorship, Mapping):
        authorship = {}
    receipt = status.get("coder_receipt")
    if not isinstance(receipt, Mapping):
        receipt = {}

    cli_ids: Optional[List[str]] = None
    for blob in (authorship, receipt, status.get("brief_dispatch")):
        found = _cli_ids_from_mapping(blob)
        if found is not None:
            cli_ids = found
            break

    action_ids: List[str] = []
    measured_actions = False
    artifacts = authorship.get("agent_artifacts")
    if isinstance(artifacts, list):
        action_ids = [cid for cid in _unique_ids(artifacts) if is_action_artifact_id(cid)]
        measured_actions = True
    explicit_action_py = _as_nonneg_int(authorship.get("action_py"))
    if explicit_action_py is not None and not measured_actions:
        measured_actions = True
        action_ids = action_ids or [f"action_{i}" for i in range(explicit_action_py)]

    action_py = len(action_ids)
    if measured_actions and explicit_action_py is not None and not artifacts:
        action_py = explicit_action_py

    if not measured_actions:
        written = _as_nonneg_int(authorship.get("agent_written"))
        if written is not None:
            measured_actions = True
            action_py = written

    cli_list = cli_ids if cli_ids is not None else []
    measured = measured_actions or cli_ids is not None
    meets = (
        action_py >= FULL_PILOT_MIN_AUTHORED_ACTIONS
        or len(cli_list) >= FULL_PILOT_MIN_AUTHORED_ACTIONS
    )
    return FullPilotAuthorship(
        action_ids=list(action_ids),
        cli_authored_ids=list(cli_list),
        action_py=action_py,
        measured=measured,
        meets_floor=bool(measured and meets),
    )


def thin_store_green_export_blocker(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
) -> Optional[str]:
    """Refuse a Store-green / full-pilot zip when authorship is below floor.

    Code-cycle prototypes (PRODUCT/STORE not run) are not this lie.
    """
    status = dict(status or {})
    floor = full_pilot_authorship_from(status, workspace)
    if not floor.below_floor:
        return None
    grade = status.get("level_grade")
    gates = grade.get("three_gate") if isinstance(grade, Mapping) else None
    if not isinstance(gates, Mapping):
        from app.factory.build.level_grade import parse_three_gate_verdict

        gates = parse_three_gate_verdict(str(status.get("detail") or ""))
    cycle = str(status.get("cycle") or "").strip().lower()
    claiming_store_green = (
        cycle == "pilot"
        or (
            str(gates.get("PRODUCT") or "") == "PASS"
            and str(gates.get("STORE") or "") == "PASS"
        )
    )
    if not claiming_store_green:
        return None
    return (
        "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the "
        f"full-pilot floor (action_py={floor.action_py}, "
        f"cli_authored_ids={len(floor.cli_authored_ids)}, "
        f"need ≥{FULL_PILOT_MIN_AUTHORED_ACTIONS}) — will not ship a "
        "Store-green / full-pilot zip"
    )
