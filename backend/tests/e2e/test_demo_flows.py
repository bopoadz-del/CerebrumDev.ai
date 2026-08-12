"""End-to-end demo flows for CerebrumDev.ai — the stranger, encoded.

  D2  health 200
  D3  drive the "generate a platform" flow: brief -> blueprint -> export
      artifact exists and is shaped like a runnable platform (action routes +
      console UI), and the export ships its own smoke tests.
  D4  an unauthenticated request to a protected route is rejected (401).

D3 runs fully offline: when no architect LLM credit is available the factory
falls back to deterministic drafting, and generation is deterministic block
composition (no LLM) by design.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402


# ── D2: health ──────────────────────────────────────────────────────────────
def test_d2_health():
    with TestClient(app) as c:
        h = c.get("/health")
        assert h.status_code == 200
        r = c.get("/ready")
        assert r.status_code in (200, 503)
        assert "status" in r.json()


# ── D3: generate a platform -> runnable artifact ────────────────────────────
def test_d3_generate_platform_artifact(tmp_path):
    from app.factory.product_architect import draft_blueprint_from_brief, generate_product

    blueprint = draft_blueprint_from_brief("Build a warehouse operations platform")
    assert blueprint is not None
    # Deterministic drafting must be disclosed, never masqueraded as an LLM draft.
    assert getattr(blueprint, "drafting_mode", None) in {"architect_llm", "keyword_fallback", "golden_steward"}

    out = tmp_path / "warehouse-export"
    result = generate_product(blueprint, out)
    assert isinstance(result, dict)

    # The export is shaped like a runnable platform, not a skeleton.
    main_py = out / "app" / "main.py"
    console = out / "app" / "static" / "console.html"
    assert main_py.is_file(), "generated export missing app/main.py"
    assert console.is_file(), "generated export missing the universal console UI"

    main_src = main_py.read_text(encoding="utf-8")
    assert "/v1/actions/" in main_src, "export has no action routes (hollow skeleton)"
    assert 'include_in_schema=False' in main_src or '@app.get("/"' in main_src, "export does not serve the console at /"

    # The export ships its own smoke suite that proves reachability.
    smoke = list(out.rglob("test_*smoke*.py")) + list(out.rglob("test_actions*.py"))
    assert smoke, "generated export ships no smoke tests"


# ── D4: protected route rejects unauthenticated caller ──────────────────────
def test_d4_protected_route_rejects_unauthenticated(monkeypatch):
    monkeypatch.setenv("RESIDENT_ENGINEER_ENABLED", "1")
    with TestClient(app) as c:
        # /v1/resident/heal executes repair actions — must reject without auth.
        resp = c.post("/v1/resident/heal", json={"action_id": "noop"})
        assert resp.status_code == 401, resp.text
        # The public status route still answers.
        assert c.get("/v1/resident/status").status_code == 200
