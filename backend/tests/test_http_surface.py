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


def test_stateless_factory_product_surface_is_gone(client):
    """Floor is the only product HTTP surface. Unused /v1/factory/product/* 404s."""
    posts = (
        "/v1/factory/product/draft",
        "/v1/factory/product/plan",
        "/v1/factory/product/generate",
    )
    for path in posts:
        res = client.post(path, json={})
        assert res.status_code == 404, (path, res.status_code, res.text)
    res = client.get("/v1/factory/product/golden/steward")
    assert res.status_code == 404, res.text


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


def test_frontend_public_headers_declare_csp_frame_ancestors_hsts():
    """M14 twin: published _headers even when the Render blueprint is not synced."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2] / "frontend" / "public" / "_headers"
    ).read_text(encoding="utf-8")
    assert "default-src 'self'" in text
    assert "frame-ancestors 'none'" in text
    assert "Strict-Transport-Security" in text
    assert "X-Content-Type-Options" in text
    assert "X-Frame-Options" in text


# ── CSP on the API origin (2026-09-10) ────────────────────────────────────
#
# Measured against live api.cerebrum-dev.com: HSTS, X-Frame-Options,
# Referrer-Policy and nosniff were present; Content-Security-Policy was not.
# The frontend has carried a CSP in _headers/render.yaml for some time and
# this origin never did.


def test_api_responses_carry_an_enforcing_csp(client):
    res = client.get("/health")
    csp = res.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "Content-Security-Policy-Report-Only" not in res.headers, (
        "an API has no sources to discover; report-only would be theatre"
    )


def test_error_responses_carry_the_csp_too(client):
    """401 is the response an unauthenticated prober sees most."""
    res = client.get("/v1/billing/status")
    assert res.status_code == 401
    assert "default-src 'none'" in res.headers["Content-Security-Policy"]


def test_docs_page_gets_a_policy_that_lets_swagger_load(client, monkeypatch):
    """default-src 'none' would leave /docs a blank page.

    The docs page pulls swagger-ui from a CDN. It is served only when
    openapi_docs_enabled(), i.e. never in a production-like ENV, but when it
    is served it has to work.
    """
    monkeypatch.setenv("ENV", "test")
    res = client.get("/docs")
    assert res.status_code == 200
    csp = res.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in csp
    assert "default-src 'none'" not in csp


def test_the_smoke_twin_reads_the_wire_not_a_file():
    """The frontend headers were declared in three files and served in none.

    Both existing twins assert file contents; this one asserts the live
    response, which is the only place the difference showed up.
    """
    import importlib.util
    from pathlib import Path

    # Repo-root scripts/, not backend/scripts/ (which is the importable one).
    script = Path(__file__).resolve().parents[2] / "scripts" / "post_deploy_smoke.py"
    assert script.is_file(), script
    spec = importlib.util.spec_from_file_location("_smoke_probe", script)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    assert "content-security-policy" in smoke.API_REQUIRED_HEADERS
    assert "strict-transport-security" in smoke.FRONTEND_REQUIRED_HEADERS
    assert callable(smoke.record_security_headers)
