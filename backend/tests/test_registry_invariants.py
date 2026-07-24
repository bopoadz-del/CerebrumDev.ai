"""Registry invariants: every documented router mounts, and feature-flagged
kernels report honest disabled status by default.

This is a CI meta-test: it runs automatically once it lands because ci.yml
runs full pytest on push to master.
"""

from __future__ import annotations

import os

import pytest

import app.main
from app.change_requests.flags import change_request_intake_enabled
from app.resident_engineer.flags import resident_engineer_enabled
from app.workbench.flags import build_mode_enabled, kimi_workbench_enabled


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    """Ensure the test environment does not accidentally enable gated kernels."""
    for name in (
        "RESIDENT_ENGINEER_ENABLED",
        "BUILD_MODE_ENABLED",
        "KIMI_WORKBENCH_ENABLED",
        "CHANGE_REQUEST_INTAKE_ENABLED",
        "RESIDENT_EMIT_CHANGE_REQUESTS",
    ):
        monkeypatch.delenv(name, raising=False)


def _route_prefixes(app) -> set:
    """Collect the unique path prefixes mounted on the FastAPI app."""
    prefixes = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path:
            # Normalize to a prefix like /v1/sessions or /v1/auth
            parts = path.strip("/").split("/")
            if len(parts) >= 2:
                prefixes.add(f"/{parts[0]}/{parts[1]}")
            elif parts and parts[0]:
                prefixes.add(f"/{parts[0]}")
    return prefixes


REQUIRED_PREFIXES = {
    "/v1/auth",
    "/v1/billing",
    "/v1/sessions",
    "/v1/domains",
    "/v1/factory",
    "/v1/resident",
    "/v1/workbench",
    "/v1/change-requests",
    "/health",
    "/ready",
}


def test_all_documented_routers_are_mounted():
    prefixes = _route_prefixes(app.main.app)
    missing = REQUIRED_PREFIXES - prefixes
    assert not missing, f"Routers missing from app: {missing}"


def test_feature_flagged_kernels_default_off():
    """Default OFF is a house security pattern; status endpoints must be honest."""
    assert resident_engineer_enabled() is False
    assert build_mode_enabled() is False
    assert kimi_workbench_enabled() is False
    assert change_request_intake_enabled() is False


def test_feature_flagged_status_endpoints_are_honest(client):
    """Status endpoints report disabled when flags are off — no fake readiness."""
    resp = client.get("/v1/resident/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["maturity"] == "APPRENTICE"
    assert "allowlisted_heal_actions" in body

    resp = client.get("/v1/workbench/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["build_mode_enabled"] is False
    assert body["kimi_workbench_enabled"] is False
    assert body["deploy_credentials"] is False

    resp = client.get("/v1/change-requests/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intake_enabled"] is False
    assert body["execution"] is False
    assert "M3 paperwork only" in body["honesty"]


def test_gated_mutation_endpoints_refuse_service_when_disabled(client):
    """When flags are off, mutation endpoints must fail closed, not silently stub."""
    # Resident engineer observe requires the flag.
    resp = client.get("/v1/resident/observe")
    assert resp.status_code == 503, resp.text

    # Workbench run requires the flag.
    resp = client.post("/v1/workbench/run", json={"request_id": "cr-123"})
    assert resp.status_code == 503, resp.text

    # Change-request queue requires the flag.
    resp = client.get("/v1/change-requests/queue")
    assert resp.status_code == 503, resp.text
