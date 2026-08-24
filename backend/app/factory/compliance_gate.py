"""Generation-time contract compliance: refuse to build an incoherent plan.

The planner already refuses UNSUPPORTED capabilities. This gate refuses the
quieter failures a plan can carry while still looking well-formed:

- a capability planned as REUSE or COMPOSE that names no block. The
  strategy says "an existing block does this"; an empty block list says
  nothing does. The product ships a capability backed by nothing. (ADAPT is
  excluded on purpose -- see BLOCK_BACKED_STRATEGIES.)
- a block referenced by a capability but absent from the plan's own
  dual-registration list.
- a capability the blueprint asked for that no plan entry answers.

Wired inside ``generate_product``, which documents itself as the single
production door, rather than at its call sites -- there are seven, and a
gate maintained by remembering to call it at seven places is the defect
this module exists to prevent.

NOT checked here, and deliberately named rather than silently absent:
**trust tier**. No block declares one. ``tier`` already exists in
``universal_kernel``'s manifest meaning the PRICING tier ("premium"), so
reusing that key would collide two unrelated meanings on the asset a trust
decision depends on. The key has to be named before this can enforce it,
and a gate that claims to check tiers while reading a price would be worse
than one that admits it does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

#: Strategies whose name asserts that an existing block does the work.
#:
#: ADAPT is deliberately absent. The planner treats a blockless ADAPT as
#: kernel/template generation ("kernel/template generation (no block ids)"),
#: so flagging it here would make this gate contradict the planner and
#: refuse a build the planner considers well-formed. A gate that disagrees
#: with the component it guards gets switched off, not fixed.
BLOCK_BACKED_STRATEGIES = frozenset({"REUSE", "COMPOSE"})

REASON_UNSUPPORTED = "unsupported_capability"
REASON_NO_BLOCK_DECLARES = "no_block_declares"
REASON_NOT_DUAL_REGISTERED = "not_dual_registered"
REASON_NOT_PLANNED = "capability_not_planned"


@dataclass(frozen=True)
class CapabilityGap:
    """One reason a plan may not be built."""

    capability_id: str
    reason: str
    detail: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "capability_id": self.capability_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ComplianceVerdict:
    compliant: bool
    gaps: Tuple[CapabilityGap, ...] = ()
    checked: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compliant": self.compliant,
            "gaps": [g.to_dict() for g in self.gaps],
            "checked": list(self.checked),
        }

    def summary(self) -> str:
        if self.compliant:
            return "compliant: %d capability(ies) checked" % len(self.checked)
        return "; ".join(
            "%s (%s)%s" % (g.capability_id, g.reason, ": " + g.detail if g.detail else "")
            for g in self.gaps
        )


class ComplianceError(RuntimeError):
    """Raised instead of generating from a plan with gaps."""

    def __init__(self, verdict: ComplianceVerdict):
        super().__init__("refusing to generate: " + verdict.summary())
        self.verdict = verdict


def evaluate_plan(plan: Any, *, blueprint: Optional[Any] = None) -> ComplianceVerdict:
    """Return a verdict on ``plan``. Never raises on a bad plan; reports it.

    ``blueprint`` is optional because the coverage check is only meaningful
    when the request is available. Its absence narrows what can be checked
    and is not itself a gap -- a check that cannot run must not report a
    pass it did not perform, nor a failure it cannot substantiate.
    """
    gaps: List[CapabilityGap] = []
    capabilities = list(getattr(plan, "capabilities", ()) or ())
    checked = [c.capability_id for c in capabilities]
    dual = set(getattr(plan, "dual_registered_blocks", ()) or ())

    for cap_id in getattr(plan, "unsupported", ()) or ():
        gaps.append(
            CapabilityGap(
                cap_id,
                REASON_UNSUPPORTED,
                "the planner resolved no strategy for this capability",
            )
        )

    for cap in capabilities:
        strategy = getattr(cap, "strategy", "")
        block_ids = list(getattr(cap, "block_ids", ()) or ())

        if strategy in BLOCK_BACKED_STRATEGIES and not block_ids:
            gaps.append(
                CapabilityGap(
                    cap.capability_id,
                    REASON_NO_BLOCK_DECLARES,
                    "strategy %s claims an existing block provides this, but "
                    "the plan names none" % strategy,
                )
            )

        missing = [b for b in block_ids if b not in dual]
        if missing:
            gaps.append(
                CapabilityGap(
                    cap.capability_id,
                    REASON_NOT_DUAL_REGISTERED,
                    "block(s) absent from the plan's dual-registered set: "
                    + ", ".join(sorted(missing)),
                )
            )

    if blueprint is not None:
        planned = {c.capability_id for c in capabilities}
        planned |= set(getattr(plan, "unsupported", ()) or ())
        for cap in getattr(blueprint, "capabilities", ()) or ():
            cap_id = getattr(cap, "id", None)
            if cap_id and cap_id not in planned:
                gaps.append(
                    CapabilityGap(
                        cap_id,
                        REASON_NOT_PLANNED,
                        "requested in the blueprint but absent from the plan",
                    )
                )
                checked.append(cap_id)

    return ComplianceVerdict(
        compliant=not gaps, gaps=tuple(gaps), checked=tuple(checked)
    )


def assert_compliant(plan: Any, *, blueprint: Optional[Any] = None) -> Any:
    """Return ``plan``, or raise ComplianceError describing every gap.

    Every gap is reported, not just the first: a caller fixing one gap at a
    time learns the shape of the problem one build at a time.
    """
    verdict = evaluate_plan(plan, blueprint=blueprint)
    if not verdict.compliant:
        raise ComplianceError(verdict)
    return plan
