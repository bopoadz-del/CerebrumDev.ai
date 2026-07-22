"""User accounts: register, login, email verification, per-user API keys."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..core import accounts_store, mailer
from ..core.auth import Principal, require_api_key
from ..core.rate_limit import check_rate_limit

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


class ResetBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LEN, max_length=256)


def _rate_limit(request: Request, bucket: str) -> None:
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(bucket, ip):
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
    else:
        verification = {
            "mode": "dev_token",
            "email_sent": False,
            "note": "SMTP not configured — verify via POST /v1/auth/verify-email with this token",
            "dev_verification_token": verify_token,
        }
    return {
        "ok": True,
        "account_id": account["account_id"],
        "email": account["email"],
        "email_verified": False,
        "login_token": login_token,
        "verification": verification,
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
    if token and not sent:
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
