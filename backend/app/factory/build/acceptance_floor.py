"""The acceptance floor: one spec, two consumers.

The Store gate graded every build against thirteen checks that the coder was
never told. Neither the writer prompt nor the C-BRIEF named a single one of
them -- measured, not assumed: ``grep`` for ``no_token_401`` across both
returned zero. So the coder discovered the floor by failing it, one rework
round per check, and every one of those rounds is a full writer pass billed
to the customer.

Putting the rules in the brief alone would just move the trust back to the
author, which is the thing the gate exists to remove. So the floor lives in
``acceptance_floor.json`` and both sides read it:

* the writer prompt renders ``requirement`` verbatim, so the agent builds
  toward the floor on the first pass;
* the Store gate takes its checklist from the same ids.

Neither may hold its own copy. ``test_acceptance_floor_is_one_source`` fails
if the two consumers ever disagree, so drift is a red build rather than a
silent regression to the state this module was written to end.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

logger = logging.getLogger(__name__)

FLOOR_REL = "acceptance_floor.json"
SCHEMA = "acceptance_floor.v2"


def floor_path() -> Path:
    return Path(__file__).resolve().parent.parent / FLOOR_REL


@lru_cache(maxsize=1)
def _load() -> Dict[str, Any]:
    data = json.loads(floor_path().read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(
            f"{FLOOR_REL}: expected schema {SCHEMA}, found {data.get('schema')!r}"
        )
    checks = data.get("checks") or []
    if not checks:
        raise ValueError(f"{FLOOR_REL}: the floor is empty")
    seen = set()
    for check in checks:
        cid = str(check.get("id") or "").strip()
        if not cid:
            raise ValueError(f"{FLOOR_REL}: a check has no id")
        if cid in seen:
            raise ValueError(f"{FLOOR_REL}: duplicate check id {cid!r}")
        seen.add(cid)
        for field in ("requirement", "check"):
            if not str(check.get(field) or "").strip():
                raise ValueError(f"{FLOOR_REL}: {cid} has no {field}")
    return data


def floor_version() -> int:
    """The version both consumers must be reading."""
    return int(_load().get("version") or 0)


def checks() -> Tuple[Mapping[str, Any], ...]:
    """Every check, in the gate's reporting order."""
    return tuple(_load()["checks"])


def check_ids() -> Tuple[str, ...]:
    """The gate's checklist. This is what ACCEPTANCE_CHECK_NAMES is."""
    return tuple(str(c["id"]) for c in checks())


def requirements() -> List[str]:
    """What the coder is told, in the same order the gate reports."""
    return [str(c["requirement"]).strip() for c in checks()]


def render_for_prompt() -> str:
    """The floor as the REQUIREMENTS block of the writer's prompt.

    Rendered verbatim from the same file the gate grades against, so the
    agent is building toward the checklist rather than guessing at it.
    """
    lines = [
        f"ACCEPTANCE FLOOR (v{floor_version()} — the Store gate grades every "
        "build against exactly these, in this order; they are not advice):",
    ]
    for check in checks():
        lines.append(f"- {check['id']}: {str(check['requirement']).strip()}")
    return "\n".join(lines)
