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

FLOOR_REL = "acceptance_floor.v2.json"
SCHEMA = "acceptance_floor.v2"

#: Build rigor, weakest to strictest. The brief declares one; the gate grades
#: the build against THAT bar, not a fixed maximum. A check is enforced at a
#: rigor when its ``min_rigor`` is at or below the active level; above it, the
#: check is advisory (reported, scored SKIP, never a veto) exactly like the
#: statically-advisory pipeline-evidence checks.
RIGOR_LEVELS: Tuple[str, ...] = ("prototype", "light", "standard", "production")

#: Absent a declared rigor, nothing is lowered: the strictest bar, so every
#: existing build grades exactly as it did before rigor existed.
DEFAULT_RIGOR = "production"


def _rigor_rank(rigor: str) -> int:
    try:
        return RIGOR_LEVELS.index(str(rigor))
    except ValueError:
        raise ValueError(
            f"unknown rigor {rigor!r}; known: {', '.join(RIGOR_LEVELS)}"
        ) from None


def normalize_rigor(value: Any) -> str:
    """A brief's declared rigor, coerced to a known level — defaulting to the
    strictest. A typo or empty value must never SILENTLY lower the bar, so it
    falls back to DEFAULT_RIGOR rather than raising on the build path."""
    text = str(value or "").strip().lower()
    return text if text in RIGOR_LEVELS else DEFAULT_RIGOR


def rigor_of(blueprint: Any) -> str:
    """The rigor THIS build declared, read off the blueprint; safe default."""
    return normalize_rigor(getattr(blueprint, "rigor", None))


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
        for field in ("requirement_text", "brief_render", "gate_fn", "check"):
            if not str(check.get(field) or "").strip():
                raise ValueError(f"{FLOOR_REL}: {cid} has no {field}")
        mr = check.get("min_rigor")
        if mr is not None and str(mr) not in RIGOR_LEVELS:
            raise ValueError(
                f"{FLOOR_REL}: {cid} has min_rigor {mr!r}, not one of "
                f"{', '.join(RIGOR_LEVELS)}"
            )
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


def advisory_ids(rigor: str = DEFAULT_RIGOR) -> Tuple[str, ...]:
    """Checks the gate REPORTS but does not fail a build on.

    A check is advisory when the floor demands evidence that no step of the
    pipeline yet produces. On 2026-09-26 three of the 21 were in that state:
    ``one_live_connector`` (nothing anywhere sets STORE_LIVE_CONNECTOR),
    ``backup_restore_roundtrip`` (the restore drill runs in the product's own
    tests, which the gate never executes) and ``bench_p95`` (the bench job
    prints STORE_BENCH_P95_MS inside product CI, which the gate never reads).
    Every build failed on all three by construction — a bar that cannot be
    cleared is not a bar, it is a wall, and it hid the checks that COULD fail.

    Advisory is a fact about the pipeline, not about the requirement: the
    requirement text still reaches the writer's prompt unchanged, the check
    still runs and still prints FAIL with its reason, and the line is still
    counted in the k/N score as SKIP. It just does not veto the build. The
    flag lives in the floor file, next to the check it describes, so both
    consumers read one source — the same reason the checklist itself does.
    The owner chose demotion over building the bridges (Gate 3b, 2026-09-26).

    ``rigor`` widens this set downward: a check whose ``min_rigor`` is stricter
    than the build's declared rigor is advisory for THIS build, on top of the
    statically-advisory ones. At ``DEFAULT_RIGOR`` the min_rigor clause can
    never fire (production is the maximum), so the set is exactly the static
    one — the safe default.
    """
    rank = _rigor_rank(rigor)
    out = []
    for c in checks():
        cid = str(c["id"])
        static = c.get("advisory") is True
        min_rigor = str(c.get("min_rigor") or RIGOR_LEVELS[0])
        if static or _rigor_rank(min_rigor) > rank:
            out.append(cid)
    return tuple(out)


def enforced_ids(rigor: str = DEFAULT_RIGOR) -> Tuple[str, ...]:
    """The checks that VETO a build at this rigor — checklist minus advisory."""
    adv = set(advisory_ids(rigor))
    return tuple(cid for cid in check_ids() if cid not in adv)


def requirements() -> List[str]:
    """What the coder is told, in the same order the gate reports."""
    return [str(c["requirement_text"]).strip() for c in checks()]


def render_for_prompt(rigor: str = DEFAULT_RIGOR) -> str:
    """The floor as the REQUIREMENTS block of the writer's prompt.

    Rendered verbatim from the same file the gate grades against, so the
    agent is building toward the checklist rather than guessing at it. The
    ``rigor`` is the bar THIS build declared: a check that is advisory at this
    rigor is marked so, so the writer spends effort on what actually vetoes
    its build and is never failed on a requirement above the declared bar.
    """
    adv = set(advisory_ids(rigor))
    lines = [
        f"ACCEPTANCE FLOOR (v{floor_version()}, rigor={rigor} — the Store gate "
        "grades this build against exactly these, in this order; a line marked "
        "[advisory at this rigor] is reported but does not fail the build):",
    ]
    for check in checks():
        line = str(check["brief_render"]).strip()
        if str(check["id"]) in adv:
            line += "  [advisory at this rigor]"
        lines.append(line)
    return "\n".join(lines)


def floor_hash() -> str:
    """The bytes both consumers must be reading.

    P3's drift lock: the brief render test and the gate render test both pin
    this, so a change to the floor that reaches only one of them is a red
    build rather than a silent divergence.
    """
    import hashlib

    return "sha256:" + hashlib.sha256(
        floor_path().read_bytes()
    ).hexdigest()


def gate_fns() -> Tuple[str, ...]:
    """The check function each entry expects the harness to define."""
    return tuple(str(c["gate_fn"]) for c in checks())
