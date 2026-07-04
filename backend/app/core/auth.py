"""Lightweight API-key auth for CerebrumDev.ai backend.

When ``CEREBRUM_DEV_API_KEY`` is unset, all requests are allowed (local dev).
When it is set, requests must include either:
- ``Authorization: Bearer <key>`` header, or
- ``X-API-Key: <key>`` header.

A startup check in ``main.py`` refuses to boot in production without a key.
"""

import os
from fastapi import Header, HTTPException, Request

_API_KEY = os.getenv("CEREBRUM_DEV_API_KEY", "").strip()


def verify_production_auth() -> None:
    """Warn if production mode is enabled without an API key.

    The app still boots so deployments don't fail on missing env vars,
    but auth will be disabled until a key is configured.
    """
    if os.getenv("ENV") == "production" and not os.getenv("CEREBRUM_DEV_API_KEY", "").strip():
        import logging
        logging.getLogger(__name__).warning(
            "CEREBRUM_DEV_API_KEY is not set. API key enforcement is disabled."
        )


def require_api_key(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """Dependency that enforces the configured API key when set."""
    if not _API_KEY:
        return

    provided = ""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = token.strip()
    if not provided and x_api_key:
        provided = x_api_key.strip()

    if provided != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
