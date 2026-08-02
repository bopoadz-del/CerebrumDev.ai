"""User accounts: register, login, email verification, per-user API keys,
plus the data-rights endpoints (export and erasure)."""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import accounts_store, billing, data_rights, mailer
from ..core.auth import Principal, require_api_key
from ..core.rate_limit import check_rate_limit_for_request

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


class RegisterBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=MIN_PASSWORD_LEN, max_length=256)


class LoginBody(BaseModel):
    email: str
    password: str


class VerifyBody(BaseModel):
    token: str


class KeyBody(BaseModel):
    label: str = ""


class ForgotBody(BaseModel):
    email: str


class DeleteAccountBody(BaseModel):
    """Re-authentication for erasure. A bearer token alone is not enough."""

    password: str = Field(..., min_length=1, max_length=256)


class ResetBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LEN, max_length=256)


def _dev_tokens_exposed() -> bool:
    """Whether verification/reset tokens may be returned in an HTTP response.

    Off unless explicitly enabled. These endpoints are public and
    unauthenticated, so returning a live ``cdr_`` reset token to the caller is
    an account takeover for anyone who knows a victim's email address.

    The old condition was "mail delivery returned False", which fails open in
    two ways: mail is unconfigured until an operator fills in the credentials
    (they ship as ``sync: false``), and ``mailer`` swallows every exception and
    reports False -- so a provider outage, a 429, or a rotated key silently
    reopens it on a correctly configured deployment. Convenience during local
    development is not worth a control that switches itself off during an
    incident.
    """
    return os.getenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _rate_limit(request: Request, bucket: str) -> None:
    # Identity comes from the socket peer unless TRUSTED_PROXY explicitly says
    # otherwise. Never trust X-Forwarded-For by default: that would make the
    # limit key caller-controlled and the throttle trivially bypassable.
    if not check_rate_limit_for_request(bucket, request):
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": "600"},
        )


def _require_user(principal: Principal) -> Principal:
    if principal.kind != "user" or not principal.account_id:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires an account credential (login token or API key)",
        )
    return principal


@router.post("/register", status_code=201)
async def register(body: RegisterBody, request: Request):
    _rate_limit(request, "register")
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    try:
        account = accounts_store.create_account(email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Email already registered") from exc

    verify_token = accounts_store.issue_verify_token(account["account_id"])
    sent = mailer.send_verification_email(email, verify_token)
    login_token = accounts_store.issue_login_token(account["account_id"])

    if sent:
        verification = {"mode": "smtp", "email_sent": True}
    elif _dev_tokens_exposed():
        verification = {
            "mode": "dev_token",
            "email_sent": False,
            "note": "SMTP not configured — verify via POST /v1/auth/verify-email with this token",
            "dev_verification_token": verify_token,
        }
    else:
        verification = {
            "mode": "unavailable",
            "email_sent": False,
            "note": "Verification email could not be sent. Request a new one shortly.",
        }
    return {
        "ok": True,
        "account_id": account["account_id"],
        "email": account["email"],
        "email_verified": False,
        "login_token": login_token,
        "verification": verification,
        "billing": billing.billing_status(account["account_id"]),
        "what_this_is_not_yet": (
            "The factory generates a working prototype — real code, tests and "
            "deploy files — not a finished production system. Third-party "
            "integrations in generated products are stubs until you connect "
            "your own credentials, deployment is a step you run rather than "
            "something that happens for you, and free-trial accounts have "
            "server-enforced caps on generations, daily chat and exports. "
            "Answers are grounding-checked: when a claim can't be verified it "
            "is withheld, not invented."
        ),
    }


@router.post("/login")
async def login(body: LoginBody, request: Request):
    _rate_limit(request, "login")
    account = accounts_store.authenticate(body.email, body.password)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {
        "ok": True,
        "account_id": account["account_id"],
        "email": account["email"],
        "email_verified": account["email_verified"],
        "login_token": accounts_store.issue_login_token(account["account_id"]),
    }


@router.get("/me")
async def me(principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    account = accounts_store.get_account(principal.account_id or "")
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, **account, "auth_kind": principal.kind}


@router.post("/verify-email")
async def verify_email(body: VerifyBody, request: Request):
    _rate_limit(request, "verify-email")
    account_id = accounts_store.confirm_verify_token(body.token.strip())
    if account_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    return {"ok": True, "account_id": account_id, "email_verified": True}


@router.post("/forgot-password")
async def forgot_password(body: ForgotBody, request: Request):
    """Issue a reset token. Response never reveals whether the email exists —
    except in dev mode (no SMTP), where the token is surfaced for the owner."""
    _rate_limit(request, "forgot-password")
    email = body.email.strip().lower()
    token = accounts_store.issue_reset_token(email)
    sent = mailer.send_password_reset_email(email, token) if token else False
    resp: dict = {
        "ok": True,
        "message": "If the email is registered, a reset link follows.",
    }
    if token and not sent and _dev_tokens_exposed():
        resp["note"] = "SMTP not configured — reset via POST /v1/auth/reset-password with this token"
        resp["dev_reset_token"] = token
    return resp


@router.post("/reset-password")
async def reset_password(body: ResetBody, request: Request):
    _rate_limit(request, "reset-password")
    account_id = accounts_store.confirm_reset_token(body.token.strip(), body.new_password)
    if account_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    return {
        "ok": True,
        "account_id": account_id,
        "message": "Password updated — sign in again (all previous sessions were closed).",
    }


@router.post("/keys", status_code=201)
async def create_key(body: KeyBody, principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    issued = accounts_store.issue_api_key(principal.account_id or "", body.label)
    return {
        "ok": True,
        "key_id": issued["key_id"],
        "api_key": issued["api_key"],
        "note": "Store this key now — it is shown once and only its hash is kept.",
    }


@router.get("/keys")
async def list_keys(principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    return {"ok": True, "keys": accounts_store.list_api_keys(principal.account_id or "")}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    if not accounts_store.revoke_api_key(principal.account_id or "", key_id):
        raise HTTPException(status_code=404, detail="Key not found or already revoked")
    return {"ok": True, "key_id": key_id, "revoked": True}


@router.get("/export")
async def export_my_data(principal: Principal = Depends(require_api_key)):
    """Everything the platform holds about the calling account, as one JSON
    document: profile, billing identifiers, usage counters, owned session ids
    and API key metadata.

    Credential material is excluded by construction — see
    ``accounts_store.export_account``. Exporting a password hash or a token
    hash would hand an attacker with a single stolen bearer token an offline
    cracking target and a permanent equality oracle.
    """
    _require_user(principal)
    payload = data_rights.export_account(principal.account_id or "")
    if payload is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, "account_id": principal.account_id, **payload}


@router.delete("/account", status_code=204)
async def delete_my_account(
    body: DeleteAccountBody,
    request: Request,
    principal: Principal = Depends(require_api_key),
):
    """Erase the calling account and everything owned by it.

    Re-authentication is mandatory: the caller must supply the account's
    current password in the body. A bearer token or API key identifies a
    *client*, and clients get stolen, cached and left in browser history.
    Irreversible destruction of every session, upload and vector index the user
    owns is not something a leaked token should be able to do on its own.

    Responses:
    * ``204`` — everything was purged.
    * ``200`` — the account row is gone but at least one content category could
      not be purged. The body names the failing categories and the session ids
      that still need manual attention. A partial purge reported as success is
      worse than an honest partial.
    * ``403`` — wrong password; nothing is touched.
    """
    _require_user(principal)
    _rate_limit(request, "delete-account")
    account_id = principal.account_id or ""
    if not accounts_store.verify_account_password(account_id, body.password):
        raise HTTPException(
            status_code=403,
            detail="Current password is required to delete this account",
        )

    report = data_rights.purge_account(account_id)
    if report["ok"]:
        return Response(status_code=204)
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "message": (
                "Account erased, but some stored content could not be removed. "
                "Contact support with this report."
            ),
            **report,
        },
    )


@router.post("/admin/retention")
async def run_retention(principal: Principal = Depends(require_api_key)):
    """Purge expired login tokens and expired verification/reset token hashes.

    Master-key only, and intentionally pull-based: no scheduler, no background
    thread. Point a cron job or an ops one-liner at it.
    """
    if principal.kind != "admin":
        raise HTTPException(status_code=403, detail="Master key required")
    return data_rights.run_retention_pass()
