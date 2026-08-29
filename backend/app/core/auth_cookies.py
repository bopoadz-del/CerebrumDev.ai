"""HttpOnly session cookie for browser login tokens.

The Floor SPA authenticates with a ``cdt_`` cookie (Secure + HttpOnly +
SameSite=Lax). API clients keep sending ``Authorization: Bearer cdt_…`` or
the master key / ``cdk_`` header — those paths are unchanged.

SameSite=Lax is the CSRF control: cross-site POSTs do not carry the cookie.
``www.cerebrum-dev.com`` → ``api.cerebrum-dev.com`` is same-site (eTLD+1),
so the live Floor fetch with ``credentials: include`` still sends it.

Host-only cookies (no ``Domain``) are the default. That is the correct
shape for an API host: the browser stores the cookie for
``api.cerebrum-dev.com`` and sends it only there. ``AUTH_COOKIE_DOMAIN``
exists if an operator ever needs ``.cerebrum-dev.com``; it is not required
for the current www/api pair and is not set on Render from this change.
"""

from __future__ import annotations

import os
from typing import Optional

from datetime import timedelta

from fastapi import Request
from fastapi.responses import Response

# Must match accounts_store login-token TTL (7d). Cookie max-age is the
# browser-side bound; the hashed row still expires independently.
_LOGIN_COOKIE_TTL = timedelta(days=7)

LOGIN_COOKIE_NAME = "cdt"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def cookie_domain() -> Optional[str]:
    raw = os.getenv("AUTH_COOKIE_DOMAIN", "").strip()
    return raw or None


def cookie_secure(request: Optional[Request] = None) -> bool:
    """Secure in production, or when the request itself is HTTPS.

    TestClient is HTTP, so ENV=test leaves Secure off unless
    ``AUTH_COOKIE_SECURE`` is set. Production (``ENV=production``/``prod``)
    always sets Secure.
    """
    explicit = os.getenv("AUTH_COOKIE_SECURE", "").strip().lower()
    if explicit in _TRUTHY:
        return True
    if explicit in _FALSY:
        return False
    env = os.getenv("ENV", "").strip().lower()
    if env in {"production", "prod"}:
        return True
    if request is not None and request.url.scheme == "https":
        return True
    return False


def cookie_max_age() -> int:
    return int(_LOGIN_COOKIE_TTL.total_seconds())


def _cookie_kwargs(request: Optional[Request] = None) -> dict:
    kwargs: dict = {
        "key": LOGIN_COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": cookie_secure(request),
        "path": "/",
        "max_age": cookie_max_age(),
    }
    domain = cookie_domain()
    if domain:
        kwargs["domain"] = domain
    return kwargs


def set_login_cookie(response: Response, token: str, request: Optional[Request] = None) -> None:
    if not token or not token.startswith("cdt_"):
        return
    response.set_cookie(value=token, **_cookie_kwargs(request))


def clear_login_cookie(response: Response, request: Optional[Request] = None) -> None:
    kwargs = _cookie_kwargs(request)
    kwargs.pop("max_age", None)
    response.delete_cookie(
        key=kwargs.pop("key"),
        path=kwargs.get("path", "/"),
        domain=kwargs.get("domain"),
        secure=kwargs.get("secure", False),
        httponly=True,
        samesite="lax",
    )


def cookie_login_token(request: Optional[Request]) -> str:
    """Return a ``cdt_`` login token from the session cookie, or empty."""
    if request is None:
        return ""
    raw = (request.cookies.get(LOGIN_COOKIE_NAME) or "").strip()
    if raw.startswith("cdt_"):
        return raw
    return ""
