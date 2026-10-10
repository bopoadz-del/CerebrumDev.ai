"""Where a build's provenance values come from.

``docs/provenance/provenance.json`` and product-dna carry ``factory_commit``
and ``blocks_commit`` so a delivered pilot can be traced back to the exact
Factory and the exact Store that produced it. Both were written as
``"unknown"``: ``converge`` read them out of ``ctx.state``, and nothing in the
build pipeline ever put them there. A buyer's IT team opening the export finds
two fields that answer "which code made this?" with "no idea".

Nothing here guesses. Each value has an ordered list of real sources and falls
back to ``UNKNOWN`` only when every one of them is genuinely unavailable --
which is the state ``gate_provenance_complete`` refuses.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

#: The fields a delivered product must be able to answer for itself.
PROVENANCE_FIELDS = ("factory_commit", "blocks_commit")


def _state(ctx: Any) -> Mapping[str, Any]:
    state = getattr(ctx, "state", None)
    return state if isinstance(state, Mapping) else {}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in ("", UNKNOWN, "none") else text


def resolve_factory_commit(ctx: Any) -> str:
    """The Factory commit that ran this build.

    ``git_head`` already falls back to ``RENDER_GIT_COMMIT``, which is what
    the production image has instead of a ``.git`` directory -- so this
    resolves on the live box, not only on a developer checkout.
    """
    explicit = _clean(_state(ctx).get("factory_commit"))
    if explicit:
        return explicit
    try:
        from app.factory.generator import git_head
        from app.factory.paths import factory_repo_root

        return _clean(git_head(factory_repo_root())) or UNKNOWN
    except Exception:  # noqa: BLE001 -- provenance never breaks a build
        logger.warning("could not resolve factory_commit", exc_info=True)
        return UNKNOWN


def resolve_blocks_commit(ctx: Any) -> str:
    """The Store commit this build vendored from.

    The lock is the authoritative answer and the last resort at the same
    time: ``blocks.lock.json`` records the Store sha the CLONER verified its
    vendored blocks against, it ships inside the image, and it is the value
    ``engine_discovery`` fetches by. A checkout's own HEAD is preferred only
    because it is the Store actually on disk for this build.
    """
    explicit = _clean(_state(ctx).get("blocks_commit"))
    if explicit:
        return explicit
    root = getattr(ctx, "blocks_root", None)
    # Only a Store tree that IS a checkout answers with its own HEAD. Without
    # its own .git, git walks up to an enclosing repo (the Factory's, on a
    # developer box) and git_head's env fallback answers with the Factory's
    # DEPLOY sha (live 2026-10-08: every export's blocks_commit equalled its
    # factory_commit). Either way the answer would be the Factory's commit.
    if root and (Path(root) / ".git").exists():
        try:
            from app.factory.generator import git_head

            head = _clean(git_head(Path(root), env_fallback=False))
            if head:
                return head
        except Exception:  # noqa: BLE001
            logger.debug("blocks_root has no git head", exc_info=True)
    try:
        from app.factory.blocks_lock import load_lock_if_present

        lock = load_lock_if_present() or {}
        return _clean((lock.get("store") or {}).get("sha")) or UNKNOWN
    except Exception:  # noqa: BLE001
        logger.warning("could not resolve blocks_commit", exc_info=True)
        return UNKNOWN


def receipt_hash(receipt: Any) -> str:
    """A stable id for one writer run.

    The worker receipt carries no id of its own, so the hash of its canonical
    form is the identifier: same run, same value, and any difference in what
    the agent did or reported changes it.
    """
    try:
        payload = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        return ""
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def resolve_provenance(ctx: Any) -> Dict[str, str]:
    """Every provenance value for this build, resolved rather than defaulted."""
    out = {
        "factory_commit": resolve_factory_commit(ctx),
        "blocks_commit": resolve_blocks_commit(ctx),
    }
    writer = _clean(_state(ctx).get("writer_receipt"))
    if writer:
        out["writer_receipt"] = writer
    return out


def missing_provenance(payload: Optional[Mapping[str, Any]]) -> list:
    """Fields a provenance document cannot answer. Empty means complete."""
    if not isinstance(payload, Mapping):
        return list(PROVENANCE_FIELDS)
    return [f for f in PROVENANCE_FIELDS if not _clean(payload.get(f))]


#: The WRITER's build record -- which run wrote which handler. The Factory's
#: own (factory_owned.BUILD_RECORD_REL): rendered before the writer pass and
#: again after it from the handlers on disk; never the writer's.
BUILD_RECORD_REL = "docs/build_provenance.json"
#: The source the record names for an agent-stamped handler.
BUILD_RECORD_AGENT_SOURCE = "coder CLI (codewhale exec)"


#: Fields other Factory steps add to the record after the writer (the N3
#: handoff's brief_dispatch, the coder's failures): carried across a re-render.
BUILD_RECORD_CARRIED_FIELDS = ("brief_dispatch", "coder_failures")


def carried_record_fields(path: Any) -> Dict[str, Any]:
    """The carried fields of the Factory's record at ``path``, if it has one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in BUILD_RECORD_CARRIED_FIELDS if k in data}


def build_record_text(
    blueprint: Any,
    handler_ids: Any = (),
    worker: Optional[Mapping[str, Any]] = None,
    carried: Optional[Mapping[str, Any]] = None,
) -> str:
    """The record, attributed from the handler ids handed in (read off the
    disk by the caller). Deterministic: the same inputs render the same bytes.
    ``carried``: fields the Factory's previous record held
    (:data:`BUILD_RECORD_CARRIED_FIELDS` only)."""
    ids = sorted({str(h) for h in handler_ids or () if str(h or "").strip()})
    n_required = len(list(getattr(blueprint, "capabilities", None) or [])) or len(ids)
    record: Dict[str, Any] = {
        "schema_version": "build_provenance.v1",
        "product_id": getattr(blueprint, "product_id", "unknown"),
        "product_name": getattr(blueprint, "product_name", ""),
        "engine": "codewhale_worker",
        "artifact_sources": {f"app/actions/{hid}.py": BUILD_RECORD_AGENT_SOURCE for hid in ids},
        # Read by the product's own acceptance.py (authorship_floor) via
        # n_required, and by the build status via the handler sources.
        "authorship": {
            "action_py": len(ids),
            "agent_artifacts": list(ids),
            "cli_authored_ids": list(ids),
        },
        "n_required": n_required,
        "written_by": "factory (rendered from the stamped handlers on disk)",
    }
    if worker:
        record["worker"] = dict(worker)
    for key in BUILD_RECORD_CARRIED_FIELDS:
        if carried and key in carried:
            record[key] = carried[key]
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def stamped_handler_ids(root: Any) -> list:
    """Handler ids under ``root`` whose AUTHORED_BY marker names the coding agent."""
    from app.factory.build.authorship import agent_written_handler_ids_in_workspace

    return agent_written_handler_ids_in_workspace(Path(root))
