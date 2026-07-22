"""P0 trust layer: accounts, per-user API keys, session ownership."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    storage_path = str(tmp_path / "storage")
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    import app.core.session_persistence as session_persistence

    monkeypatch.setattr(session_persistence, "STORAGE_PATH", storage_path)
    monkeypatch.setattr("app.core.auth._API_KEY", "")
    monkeypatch.delenv("SMTP_HOST", raising=False)

    from app.routers import accounts, sessions

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(sessions.router, prefix="/v1/sessions")
    return TestClient(app)


def _register(client, email="dev@example.com", password="pilot-pass-123"):
    res = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def test_register_login_verify_and_me(client):
    body = _register(client)
    assert body["account_id"].startswith("acct_")
    assert body["email_verified"] is False
    # Dev mode: no SMTP configured → verification token surfaced honestly.
    token = body["verification"]["dev_verification_token"]
    assert token.startswith("cdv_")

    res = client.post("/v1/auth/verify-email", json={"token": token})
    assert res.status_code == 200
    assert res.json()["email_verified"] is True

    res = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {body['login_token']}"}
    )
    assert res.status_code == 200
    me = res.json()
    assert me["email"] == "dev@example.com"
    assert me["email_verified"] is True

    res = client.post(
        "/v1/auth/login", json={"email": "dev@example.com", "password": "wrong-pass"}
    )
    assert res.status_code == 401
    res = client.post(
        "/v1/auth/login", json={"email": "dev@example.com", "password": "pilot-pass-123"}
    )
    assert res.status_code == 200
    assert res.json()["login_token"].startswith("cdt_")


def test_duplicate_email_rejected(client):
    _register(client)
    res = client.post(
        "/v1/auth/register",
        json={"email": "dev@example.com", "password": "pilot-pass-123"},
    )
    assert res.status_code == 409


def test_short_password_rejected(client):
    res = client.post(
        "/v1/auth/register", json={"email": "a@b.co", "password": "short"}
    )
    assert res.status_code == 422  # pydantic min_length


def test_api_keys_and_session_ownership(client):
    a = _register(client, "a@example.com")
    b = _register(client, "b@example.com")

    key = client.post(
        "/v1/auth/keys",
        json={"label": "ci"},
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert key.status_code == 201, key.text
    api_key = key.json()["api_key"]
    assert api_key.startswith("cdk_")

    # A creates a session with the API key — owned by A's account.
    created = client.post("/v1/sessions/", headers={"X-API-Key": api_key})
    assert created.status_code == 200, created.text
    sess = created.json()
    assert sess["user_id"] == a["account_id"]

    # A can read it; B gets 404 (existence is not leaked).
    ok = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert ok.status_code == 200
    denied = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": f"Bearer {b['login_token']}"},
    )
    assert denied.status_code == 404

    # Listing: A sees the session; B's list is empty.
    mine = client.get(
        "/v1/sessions/", headers={"Authorization": f"Bearer {a['login_token']}"}
    )
    assert any(s["session_id"] == sess["session_id"] for s in mine.json())
    other = client.get(
        "/v1/sessions/", headers={"Authorization": f"Bearer {b['login_token']}"}
    )
    assert other.json() == []

    # Revoke the key → it stops resolving (dev mode falls back to dev principal).
    key_id = key.json()["key_id"]
    res = client.delete(
        f"/v1/auth/keys/{key_id}",
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert res.status_code == 200
    res = client.post("/v1/sessions/", headers={"X-API-Key": api_key})
    assert res.json()["user_id"] == "dev-local"


def test_master_key_admin_access(client, monkeypatch):
    monkeypatch.setattr("app.core.auth._API_KEY", "master-secret")
    a = _register(client, "owner@example.com")
    created = client.post(
        "/v1/sessions/", headers={"Authorization": f"Bearer {a['login_token']}"}
    )
    sess = created.json()

    # Master key reads any session.
    res = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": "Bearer master-secret"},
    )
    assert res.status_code == 200
    # User credential still resolves alongside the master key.
    res = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert res.status_code == 200
    # Garbage credential → 401.
    res = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": "Bearer garbage"},
    )
    assert res.status_code == 401


def test_me_requires_account_credential(client):
    res = client.get("/v1/auth/me")
    assert res.status_code == 403
