"""Production HTTP surface: no public OpenAPI, baseline security headers."""

from __future__ import annotations

import app.main as main


def test_openapi_docs_enabled_defaults_off_in_production(monkeypatch):
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("ENV", "production")
    assert main.openapi_docs_enabled() is False
    monkeypatch.setenv("ENV", "prod")
    assert main.openapi_docs_enabled() is False


def test_openapi_docs_enabled_in_dev_and_test(monkeypatch):
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("ENV", "test")
    assert main.openapi_docs_enabled() is True
    monkeypatch.setenv("ENV", "development")
    assert main.openapi_docs_enabled() is True


def test_openapi_docs_explicit_override(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "1")
    assert main.openapi_docs_enabled() is True
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "0")
    assert main.openapi_docs_enabled() is False


def test_production_env_404s_docs_redoc_and_openapi(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        res = client.get(path)
        assert res.status_code == 404, path
        assert res.json()["detail"] == "Not Found"


def test_health_carries_baseline_security_headers(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert "camera=()" in res.headers["Permissions-Policy"]


def test_production_env_sets_hsts(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    res = client.get("/health")
    assert res.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_render_yaml_declares_frontend_security_headers():
    import yaml

    from pathlib import Path

    render = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "render.yaml").read_text(
            encoding="utf-8"
        )
    )
    frontend = [s for s in render["services"] if s.get("name") == "cerebrumdev-frontend"]
    assert frontend, "cerebrumdev-frontend missing from render.yaml"
    names = {h["name"] for h in frontend[0].get("headers") or []}
    assert "X-Content-Type-Options" in names
    assert "X-Frame-Options" in names
    assert "Strict-Transport-Security" in names
    assert "Content-Security-Policy" in names
    csp = next(
        h["value"]
        for h in frontend[0]["headers"]
        if h["name"] == "Content-Security-Policy"
    )
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
