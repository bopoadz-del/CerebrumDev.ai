"""Generic domain-action contract for Cerebrum-Blocks.

This package defines a domain-neutral, strongly typed contract for *actions*
(deliverable/analysis operations) that domain kits expose to a host runtime
(e.g. the CerebrumDev.ai platform factory).

The contract is intentionally generic: the host runtime dispatches actions
purely by their registered :class:`ActionSpec`. Adding a new action to a kit
must never require editing a central dispatcher — the host discovers and
executes actions through :class:`ActionRegistry` and :func:`execute_action`.

Public surface:

* :class:`ActionStatus`     – closed status enum
* :class:`ActionContext`    – trusted runtime context (never model-supplied)
* :class:`ActionDependency` – a missing pre-condition
* :class:`ActionEvidence`   – a citation/evidence reference
* :class:`ActionOutcome`    – what a handler returns
* :class:`ActionSpec`       – the action declaration
* :class:`ActionResult`     – the serialized execution record
* :class:`ActionRegistry`   – discovery + resolution
* :func:`execute_action`    – the generic execution engine
"""

from app.retailops.kit.contract.models import (
    ActionContext,
    ActionDependency,
    ActionEvidence,
    ActionOutcome,
    ActionResult,
    ActionSpec,
    ActionStatus,
    RESERVED_CONTEXT_KEYS,
)
from app.retailops.kit.contract.registry import ActionRegistry, DuplicateActionError, InvalidActionError
from app.retailops.kit.contract.runtime import execute_action

__all__ = [
    "ActionContext",
    "ActionDependency",
    "ActionEvidence",
    "ActionOutcome",
    "ActionResult",
    "ActionSpec",
    "ActionStatus",
    "RESERVED_CONTEXT_KEYS",
    "ActionRegistry",
    "DuplicateActionError",
    "InvalidActionError",
    "execute_action",
]
