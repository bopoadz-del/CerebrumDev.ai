"""Billing: subscription/trial status for the signed-in account.

Stripe checkout + webhook land in the next slice; this router is their home
(``/v1/billing/checkout``, ``/v1/billing/portal``, ``/v1/billing/webhook``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core import billing
from ..core.auth import Principal, require_api_key

router = APIRouter()


@router.get("/status")
async def status(principal: Principal = Depends(require_api_key)):
    if principal.kind != "user" or not principal.account_id:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires an account credential (login token or API key)",
        )
    snap = billing.billing_status(principal.account_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, "billing": snap}
