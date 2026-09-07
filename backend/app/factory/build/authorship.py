"""Fail-closed authorship accounting — CLI keep-path is agent-written.

sess_4e1ec7afa3894dc8 on tip 160af0e: harvest kept four FACTORY_CODE_CLI
handlers tagged ``coder CLI (/usr/local/bin/kimi)``, but writer_contract
and Floor authorship only counted ``coder LLM`` prefixes. The operator
saw ``0 by the coding agent, 27 templated`` after a ~16-minute CLI keep.

A capability kept from CLI is coding-agent work. It must not also sit in
the templated inventory for the same run.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = (
    "DualListedAuthorshipError",
    "coding_agent_artifact_ids",
    "dual_listed_capability_ids",
    "exclusive_authorship_caps",
    "cli_authored_ids_from",
    "is_coding_agent_source",
    "kept_handler_ids_from",
    "promote_cli_keep_ids",
    "refuse_dual_listed_caps",
    "writer_authorship_counts",
    "writer_contract_role_detail",
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
