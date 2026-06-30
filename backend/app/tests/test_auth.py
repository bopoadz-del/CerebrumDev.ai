import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.auth import require_api_key, verify_production_auth


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.core.auth._API_KEY", "")
    router = APIRouter()

    @router.get("/protected")
    async def protected():
        return {"ok": True}

    app = FastAPI()
    app.include_router(router, prefix="/v1", dependencies=[Depends(require_api_key)])
    return TestClient(app)


def test_no_key_required_when_unset(client, monkeypatch):
    monkeypatch.setattr("app.core.auth._API_KEY", "")
    response = client.get("/v1/protected")
    assert response.status_code == 200


def test_bearer_key_required(client, monkeypatch):
    monkeypatch.setattr("app.core.auth._API_KEY", "secret123")

    response = client.get("/v1/protected")
    assert response.status_code == 401

    response = client.get("/v1/protected", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401

    response = client.get("/v1/protected", headers={"Authorization": "Bearer secret123"})
    assert response.status_code == 200


def test_x_api_key_header(client, monkeypatch):
    monkeypatch.setattr("app.core.auth._API_KEY", "secret123")
    response = client.get("/v1/protected", headers={"X-API-Key": "secret123"})
    assert response.status_code == 200


def test_verify_production_auth_requires_key(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CEREBRUM_DEV_API_KEY must be set in production"):
        verify_production_auth()


def test_verify_production_auth_passes_with_key(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "secret")
    verify_production_auth()


def test_verify_production_auth_passes_in_dev(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    verify_production_auth()
