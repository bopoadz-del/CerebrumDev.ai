"""Stripe billing (P3 slice B): checkout, customer portal, webhook handling.

Env:
  STRIPE_SECRET_KEY      — API key (sk_test_... / sk_live_...)
  STRIPE_PRICE_ID        — recurring Price for the subscription (price_...)
  STRIPE_WEBHOOK_SECRET  — signing secret for /v1/billing/webhook (whsec_...)

Checkout success/cancel return to ``FRONTEND_URL/billing``.

Everything degrades honestly: with Stripe unconfigured, checkout/portal
return 503 ``stripe_not_configured`` and the webhook is inert. Account state
only ever changes through ``accounts_store.set_subscription`` — the same seam
tests and ops use, so the Stripe path adds no new mutation surface.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import stripe

from . import accounts_store


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def secret_key() -> str:
    return _env("STRIPE_SECRET_KEY")


def price_id() -> str:
    return _env("STRIPE_PRICE_ID")


def webhook_secret() -> str:
    return _env("STRIPE_WEBHOOK_SECRET")


def stripe_configured() -> bool:
    return bool(secret_key() and price_id())


def _frontend_url() -> str:
    return _env("FRONTEND_URL") or "http://localhost:5173"


def _configure() -> None:
    stripe.api_key = secret_key()


def create_checkout_session(account_id: str, email: str) -> str:
    """Create a subscription Checkout Session; returns the hosted URL.

    Returning customers (already have a Stripe customer id) are attached to
    their existing customer so their payment method carries over.
    """
    _configure()
    fields = accounts_store.subscription_fields(account_id) or {}
    params: Dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id(), "quantity": 1}],
        "client_reference_id": account_id,
        "metadata": {"account_id": account_id},
        "subscription_data": {"metadata": {"account_id": account_id}},
        "success_url": f"{_frontend_url()}/billing?checkout=success",
        "cancel_url": f"{_frontend_url()}/billing?checkout=cancel",
    }
    if fields.get("stripe_customer_id"):
        params["customer"] = fields["stripe_customer_id"]
    else:
        params["customer_email"] = email
    session = stripe.checkout.Session.create(**params)
    return session.url


def create_portal_session(account_id: str) -> Optional[str]:
    """Customer portal URL (update card, cancel, invoices); None if the
    account has never checked out (no Stripe customer yet)."""
    _configure()
    fields = accounts_store.subscription_fields(account_id) or {}
    customer = fields.get("stripe_customer_id")
    if not customer:
        return None
    session = stripe.billing_portal.Session.create(
        customer=customer,
        return_url=f"{_frontend_url()}/billing",
    )
    return session.url


def construct_webhook_event(payload: bytes, signature: str) -> Dict[str, Any]:
    """Verify the Stripe signature and return the event (raises on failure)."""
    return stripe.Webhook.construct_event(payload, signature, webhook_secret())


# Stripe subscription statuses mapped onto our four states. Anything that
# isn't clearly good ('active'/'trialing') must not grant access.
_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
    "paused": "canceled",
}


def _account_id_for(customer: Optional[str], metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve our account from a Stripe customer id, falling back to the
    metadata we stamp on checkout/subscription."""
    if customer:
        account = accounts_store.account_for_stripe_customer(customer)
        if account is not None:
            return account["account_id"]
    if metadata:
        account_id = (metadata.get("account_id") or "").strip()
        if account_id:
            return account_id
    return None


def handle_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a verified Stripe event to account state.

    All mutations are idempotent sets, so Stripe's at-least-once delivery is
    safe. Unknown/irrelevant events are acked (handled=False) so Stripe stops
    retrying them.
    """
    etype = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        account_id = data.get("client_reference_id") or (data.get("metadata") or {}).get(
            "account_id"
        )
        if not account_id:
            return {"handled": False, "type": etype, "reason": "no account reference"}
        accounts_store.set_subscription(
            account_id,
            "active",
            stripe_customer_id=data.get("customer"),
            stripe_subscription_id=data.get("subscription"),
        )
        return {"handled": True, "type": etype, "account_id": account_id}

    if etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        account_id = _account_id_for(data.get("customer"), data.get("metadata"))
        if not account_id:
            return {"handled": False, "type": etype, "reason": "unknown customer"}
        if etype.endswith("deleted"):
            status = "canceled"
        else:
            status = _STATUS_MAP.get(data.get("status", ""), "past_due")
        accounts_store.set_subscription(
            account_id,
            status,
            stripe_customer_id=data.get("customer"),
            stripe_subscription_id=data.get("id"),
        )
        return {"handled": True, "type": etype, "account_id": account_id, "status": status}

    if etype == "invoice.payment_failed":
        account_id = _account_id_for(data.get("customer"), None)
        if not account_id:
            return {"handled": False, "type": etype, "reason": "unknown customer"}
        accounts_store.set_subscription(account_id, "past_due")
        return {"handled": True, "type": etype, "account_id": account_id, "status": "past_due"}

    return {"handled": False, "type": etype, "reason": "unhandled event type"}
