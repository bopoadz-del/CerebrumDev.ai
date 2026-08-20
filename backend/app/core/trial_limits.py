"""Server-side trial quotas — the boundary of what a free account can do.

Counters (env-overridable):
- ``generation``    lifetime product generations   (TRIAL_GENERATION_LIMIT, 3)
- ``chat_message``  chat messages per day          (TRIAL_CHAT_MESSAGES_PER_DAY, 100)
- ``export``        lifetime prototype exports     (TRIAL_EXPORT_LIMIT, 5)
- ``draft``         blueprint drafts/plans per day (TRIAL_DRAFT_LIMIT, 20)

Applies to real user accounts without an active subscription. Active
subscribers, admin/master-key, local-dev principals, and the ops smoke
accounts (``factory-smoke-*@cerebrum-dev.invalid``) are exempt. Enforced
here, server-side — never in the UI. The deploy-gate principals must not
burn the public trial cap or the live smoke cannot be re-run.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from . import accounts_store

# Must match backend/app/routers/accounts.py smoke-login principals.
OPS_SMOKE_EMAILS = frozenset(
    {
        "factory-smoke-a@cerebrum-dev.invalid",
        "factory-smoke-b@cerebrum-dev.invalid",
    }
)

# counter -> (env var, default limit, scope)
TRIAL_COUNTERS: Dict[str, tuple] = {
    "generation": ("TRIAL_GENERATION_LIMIT", 3, "lifetime"),
    "chat_message": ("TRIAL_CHAT_MESSAGES_PER_DAY", 100, "daily"),
    "export": ("TRIAL_EXPORT_LIMIT", 5, "lifetime"),
    # Drafting is an iterative, paid LLM action: daily like chat, not lifetime
    # like generation, so refining a brief cannot burn the generation budget.
    "draft": ("TRIAL_DRAFT_LIMIT", 20, "daily"),
}


class TrialLimitExceeded(HTTPException):
    def __init__(self, counter: str, limit: int, scope: str):
        super().__init__(
            status_code=429,
            detail={
                "error": "trial_limit_reached",
                "counter": counter,
                "limit": limit,
                "scope": scope,
                "message": (
                    f"Free-trial limit reached for {counter} "
                    f"({limit} per {'day' if scope == 'daily' else 'trial'}). "
                    "Subscribe to continue."
                ),
                "upgrade": "/v1/billing/checkout",
            },
        )


def _limit(counter: str) -> tuple[int, str]:
    env, default, scope = TRIAL_COUNTERS[counter]
    try:
        return max(0, int(os.getenv(env, str(default)))), scope
    except ValueError:
        return default, scope


def _period(scope: str) -> str:
    if scope == "daily":
        return datetime.now(timezone.utc).date().isoformat()
    return "lifetime"


def _is_limited_account(account_id: Optional[str]) -> bool:
    """Quotas bind real accounts without an active subscription only."""
    if not account_id:
        return False
    fields = accounts_store.subscription_fields(account_id)
    if fields is None:
        return False
    email = str(fields.get("email") or "").strip().lower()
    if email in OPS_SMOKE_EMAILS:
        return False
    return (fields.get("subscription_status") or "none").strip().lower() != "active"


def consume(account_id: str, counter: str) -> Dict[str, Any]:
    """Spend one unit of ``counter``; report whether it was within the limit."""
    limit, scope = _limit(counter)
    value = accounts_store.increment_usage(account_id, counter, _period(scope))
    return {
        "allowed": value <= limit,
        "used": value,
        "limit": limit,
        "remaining": max(0, limit - value),
        "scope": scope,
    }


def require_within_limit(account_id: Optional[str], counter: str) -> None:
    """Server-side quota gate. Raises 429 TrialLimitExceeded over the limit."""
    if not _is_limited_account(account_id):
        return
    outcome = consume(account_id, counter)
    if not outcome["allowed"]:
        limit, scope = _limit(counter)
        raise TrialLimitExceeded(counter, limit, scope)


def quota_snapshot(account_id: str) -> Dict[str, Dict[str, Any]]:
    """Read-only usage snapshot for the account (no increments)."""
    snapshot: Dict[str, Dict[str, Any]] = {}
    for counter in TRIAL_COUNTERS:
        limit, scope = _limit(counter)
        used = accounts_store.get_usage(account_id, counter, _period(scope))
        snapshot[counter] = {
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "scope": scope,
        }
    return snapshot
