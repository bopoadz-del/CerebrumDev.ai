"""Auth resolution for cerebrum_cli.

Resolution order: flag > env > config file.
Credentials:
  --token / CEREBRUM_TOKEN
  --api-key / CEREBRUM_API_KEY (also read from config file)
  --email + --password / CEREBRUM_EMAIL + CEREBRUM_PASSWORD (login POST)
"""

from __future__ import annotations

import os
import sys

import httpx

from .config import load_config


def resolve_auth(
    base_url: str,
    token: str | None = None,
    api_key: str | None = None,
    email: str | None = None,
    password: str | None = None,
) -> str:
    """Return a Bearer credential or exit with a clear message."""
    cfg = load_config()

    cred = (
        token
        or os.getenv("CEREBRUM_TOKEN")
        or api_key
        or os.getenv("CEREBRUM_API_KEY")
        or cfg.get("api_key")
    )
    if cred:
        return cred

    email = email or os.getenv("CEREBRUM_EMAIL")
    password = password or os.getenv("CEREBRUM_PASSWORD")
    if email and password:
        try:
            r = httpx.post(
                f"{base_url}/v1/users/login",
                json={"email": email, "password": password},
                timeout=30,
            )
            r.raise_for_status()
            tok = r.json()["token"]
            print(f"[auth] logged in as {email}", file=sys.stderr)
            return tok
        except Exception as exc:
            sys.exit(f"Login failed: {exc}")

    sys.exit(
        "No credentials. Use --token / --api-key / --email+--password "
        "(or CEREBRUM_TOKEN / CEREBRUM_API_KEY / CEREBRUM_EMAIL+CEREBRUM_PASSWORD env vars, "
        "or api_key in ~/.cerebrum/config.toml)."
    )


def bearer_header(cred: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {cred}"}
