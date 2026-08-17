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
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # No master key now means "refuse", not "let everyone in", so an
    # unauthenticated fixture has to ask for the dev principal.
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def test_session_product_steward_golden_flow(client, monkeypatch, tmp_path):
    """The TEMPLATE path's golden flow (provenance.json, kernel ActionOutcome
    actions, command_center.tsx) -- all template-only artifacts.

    Pinned to that engine. Note for the next reader: before pinning, this
    test PASSED locally and failed in CI, because a stale output directory
    from an earlier template-era run still satisfied the assertions on a
    developer machine while a fresh CI container told the truth. The runner
    path's session contract is covered by test_production_uses_the_runner.py
    and the build-status endpoint test below.
    """
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
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

    # No caller-supplied output_dir: an absolute path chosen by the client was
    # a recursive-delete primitive, so the server picks the location and the
    # test asserts against what it reports back.
    r = client.post(
        "/v1/sessions/sess_product_1/product/generate",
        json={},
    )
    assert r.status_code == 200, r.text
    gen = r.json()["generation"]
    assert gen["product_id"] == "cerebrum-steward"
    out = Path(gen["output_dir"])
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

def test_runner_build_reports_progress_and_gates_the_download(client, monkeypatch):
    """The production HTTP contract for a runner build.

    The UI depends on exactly this: generate returns immediately with a
    building state, build-status reports progress off the ledger, and the
    package endpoint refuses (409) until the build has passed its gates --
    never handing over a half-written tree.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_BUILD_ENGINE", raising=False)
    create_session("sess_runner_1", "tester")

    r = client.post(
        "/v1/sessions/sess_runner_1/product/draft",
        json={"brief": "Build a warehouse operations platform"},
    )
    assert r.status_code == 200, r.text
    assert client.post("/v1/sessions/sess_runner_1/product/plan").status_code == 200
    assert (
        client.post(
            "/v1/sessions/sess_runner_1/product/approve", json={"approve": True}
        ).status_code
        == 200
    )

    r = client.post("/v1/sessions/sess_runner_1/product/generate", json={})
    assert r.status_code == 200, r.text
    gen = r.json()["generation"]
    # The client must be able to tell "started" from "finished".
    assert gen["engine"] == "runner"
    assert gen["build"]["state"] in ("building", "succeeded", "failed")

    status_res = client.get("/v1/sessions/sess_runner_1/product/build-status")
    assert status_res.status_code == 200, status_res.text
    build = status_res.json()["build"]
    assert build["state"] in ("building", "succeeded", "failed")
    assert build["phases_total"] == 5

    # While building, the download must be refused rather than shipping a
    # splice of two writer passes. Re-read the state alongside the call
    # instead of trusting the earlier read: the build runs on a background
    # thread and can finish between the two, which made an earlier version
    # of this assertion flaky (it demanded 409 from an already-finished
    # build). The invariant is the PAIRING, not a fixed status code.
    pkg = client.get("/v1/sessions/sess_runner_1/product/package")
    state_now = client.get("/v1/sessions/sess_runner_1/product/build-status").json()[
        "build"
    ]["state"]
    if pkg.status_code == 409:
        detail = pkg.json()["detail"]
        assert ("still being built" in detail) or ("did not pass its gates" in detail)
        assert state_now in ("building", "failed", "stalled"), state_now
    else:
        # The only way a download may succeed is a build that passed.
        assert pkg.status_code == 200, pkg.text
        assert state_now == "succeeded", state_now

