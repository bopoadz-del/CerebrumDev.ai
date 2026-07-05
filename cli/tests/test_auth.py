import os
from unittest import mock

import httpx
import pytest

from cerebrum_cli import auth


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in ("CEREBRUM_TOKEN", "CEREBRUM_API_KEY", "CEREBRUM_EMAIL", "CEREBRUM_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def test_resolve_auth_prefers_flag_token():
    assert auth.resolve_auth("http://x", token="tok") == "tok"


def test_resolve_auth_prefers_env_token(monkeypatch):
    monkeypatch.setenv("CEREBRUM_TOKEN", "env_tok")
    assert auth.resolve_auth("http://x") == "env_tok"


def test_resolve_auth_flag_token_beats_env(monkeypatch):
    monkeypatch.setenv("CEREBRUM_TOKEN", "env_tok")
    assert auth.resolve_auth("http://x", token="flag_tok") == "flag_tok"


def test_resolve_auth_api_key_from_flag():
    assert auth.resolve_auth("http://x", api_key="ak") == "ak"


def test_resolve_auth_api_key_from_env(monkeypatch):
    monkeypatch.setenv("CEREBRUM_API_KEY", "env_ak")
    assert auth.resolve_auth("http://x") == "env_ak"


def test_resolve_auth_api_key_from_config(tmp_path, monkeypatch):
    from cerebrum_cli import config

    path = tmp_path / "config.toml"
    path.write_text('api_key = "cfg_ak"\n')
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    assert auth.resolve_auth("http://x") == "cfg_ak"


def test_resolve_auth_order_token_over_api_key(monkeypatch):
    monkeypatch.setenv("CEREBRUM_TOKEN", "tok")
    monkeypatch.setenv("CEREBRUM_API_KEY", "ak")
    assert auth.resolve_auth("http://x", api_key="flag_ak") == "tok"


def test_resolve_auth_email_login(respx_mock, monkeypatch):
    monkeypatch.setenv("CEREBRUM_EMAIL", "a@b.com")
    monkeypatch.setenv("CEREBRUM_PASSWORD", "pw")
    route = respx_mock.post("http://x/v1/users/login").respond(200, json={"token": "jwt"})
    assert auth.resolve_auth("http://x") == "jwt"
    assert route.called


def test_resolve_auth_login_failure(respx_mock, monkeypatch):
    monkeypatch.setenv("CEREBRUM_EMAIL", "a@b.com")
    monkeypatch.setenv("CEREBRUM_PASSWORD", "pw")
    respx_mock.post("http://x/v1/users/login").respond(401)
    with pytest.raises(SystemExit):
        auth.resolve_auth("http://x")


def test_resolve_auth_missing_credentials():
    with pytest.raises(SystemExit):
        auth.resolve_auth("http://x")


def test_bearer_header():
    assert auth.bearer_header("tok") == {"Authorization": "Bearer tok"}
