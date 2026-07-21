"""Session-scoped Design Product API — Steward golden path."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.session_store import create_session


ROOT = Path(__file__).resolve().parents[3]
BLOCKS = Path("/home/ubuntu/repos/Cerebrum-Blocks")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(BLOCKS))
    monkeypatch.setenv("ENV", "test")
    # auth module caches the key at import time — clear the cached value for tests
    monkeypatch.setattr("app.core.auth._API_KEY", "")
    return TestClient(app)


def test_session_product_steward_golden_flow(client, monkeypatch, tmp_path):
    create_session("sess_product_1", "tester")
    out = tmp_path / "gen"
    # draft
    r = client.post(
        "/v1/sessions/sess_product_1/product/draft",
        json={
            "brief": "Generate Cerebrum-Steward private estate operations",
            "vertical_hint": "estate",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["blueprint"]["product_id"] == "cerebrum-steward"
    assert body["source"] == "golden_steward"

    r = client.post("/v1/sessions/sess_product_1/product/plan")
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["product_id"] == "cerebrum-steward"
    strategies = {c["capability_id"]: c["strategy"] for c in plan["capabilities"]}
    assert strategies["estate_registry"] == "COMPOSE"
    assert "UNSUPPORTED" not in strategies.values()

    r = client.post(
        "/v1/sessions/sess_product_1/product/approve", json={"approve": True}
    )
    assert r.status_code == 200

    r = client.post(
        "/v1/sessions/sess_product_1/product/generate",
        json={"output_dir": str(out)},
    )
    assert r.status_code == 200, r.text
    gen = r.json()["generation"]
    assert gen["product_id"] == "cerebrum-steward"
    assert (out / "app" / "main.py").exists()
    assert (out / "docs" / "provenance" / "provenance.json").exists()
    # kernel-shaped action (not bare echo-only)
    action = (out / "app" / "actions" / "estate_registry.py").read_text()
    assert "ActionOutcome" in action
    assert "ActionEvidence" in action
    ui = (out / "frontend" / "src" / "modules" / "command_center.tsx").read_text()
    assert "CAPABILITIES" in ui
    assert "data-module" in ui

    st = client.get("/v1/sessions/sess_product_1/product")
    assert st.status_code == 200
    assert st.json()["blueprint_approved"] is True
    assert st.json()["generation"]["inputs_hash"]


def test_generate_requires_approval(client):
    create_session("sess_product_2", "tester")
    client.post(
        "/v1/sessions/sess_product_2/product/draft",
        json={"brief": "Build steward estate platform"},
    )
    r = client.post("/v1/sessions/sess_product_2/product/generate", json={})
    assert r.status_code == 400
