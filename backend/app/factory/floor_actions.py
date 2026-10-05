"""Typed Floor actions: what the user asked the Factory to DO.

The Floor's controls (Approve, Continue, Run pilot, Draft, the feature-list
editor, rename, rigor, vertical) send an explicit ``action`` -- and a
``value`` where the action takes one -- with the chat request. The Factory
dispatches on that field. It never reads the user's words to decide an
action: free-typed text goes to the Floor chat LLM, whose answer is itself a
typed decision, and with no LLM configured free text gets an honest pointer
to the controls. (The regexes this replaces matched "approve", "go ahead",
"rename to ...", "make it a prototype" ... in the message and decided by
them.)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class FloorAction(str, Enum):
    """Every action a Floor control can send."""

    APPROVE = "approve"
    CONTINUE = "continue"
    RUN_PILOT = "run_pilot"
    DRAFT = "draft"
    ADD_CAPABILITY = "add_capability"
    REMOVE_CAPABILITY = "remove_capability"
    RENAME = "rename"
    SET_VERTICAL = "set_vertical"
    SET_RIGOR = "set_rigor"
    LIST_CAPABILITIES = "list_capabilities"
    #: The legacy kit-chain configurator (chain suggestion over the user's
    #: documents) -- reached by this typed action, never by kit vocabulary.
    CHAIN = "chain"


class RigorLevel(str, Enum):
    """The build grades a blueprint can declare (ProductBlueprint.rigor)."""

    PROTOTYPE = "prototype"
    LIGHT = "light"
    STANDARD = "standard"
    PRODUCTION = "production"


#: Actions that edit a pending blueprint (feature list, name, grade, vertical).
REFINEMENT_ACTIONS = frozenset(
    {
        FloorAction.ADD_CAPABILITY,
        FloorAction.REMOVE_CAPABILITY,
        FloorAction.RENAME,
        FloorAction.SET_VERTICAL,
        FloorAction.SET_RIGOR,
        FloorAction.LIST_CAPABILITIES,
    }
)

#: Refinements that need a value (a capability id, a name, a vertical, a grade).
VALUE_REQUIRED = frozenset(REFINEMENT_ACTIONS - {FloorAction.LIST_CAPABILITIES})

#: Actions that start or resume the coding agent.
RUN_ACTIONS = frozenset({FloorAction.APPROVE, FloorAction.CONTINUE, FloorAction.RUN_PILOT})


class FloorActionError(ValueError):
    """An action the Floor does not define, or one missing its value."""


def parse_action(raw: Optional[str]) -> Optional[FloorAction]:
    """The typed action, or None when the request carries none.

    An unknown action is refused, never guessed at.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return FloorAction(text)
    except ValueError as exc:
        raise FloorActionError(
            f"unknown Floor action {text!r}; expected one of "
            + ", ".join(a.value for a in FloorAction)
        ) from exc


def parse_rigor(raw: Optional[str]) -> RigorLevel:
    try:
        return RigorLevel((raw or "").strip())
    except ValueError as exc:
        raise FloorActionError(
            f"unknown build grade {raw!r}; expected one of "
            + ", ".join(r.value for r in RigorLevel)
        ) from exc
