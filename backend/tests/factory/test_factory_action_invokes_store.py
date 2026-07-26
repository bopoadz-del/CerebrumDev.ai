"""Anti-hollowness guard #2: the generated ACTION must invoke real blocks.

Root cause of the second layer of the hollow retail product (2026-07-26): even
after the vendoring fix (#119), every generated action's handle() returned a
canned string — `"<cap> executed via Factory template"` with `ok: True` — and
called nothing. The product reported success while doing zero work.

The live-proof export confirmed it: all four retail actions still carried the
canned string and zero store calls.

These tests pin the contract that would have caught it:
- The generated action source must NOT contain the canned template string and
  MUST route to the store's /v1/execute.
- With no CEREBRUM_API_URL, handle() degrades to DEPENDENCY_REQUIRED honestly —
  it never fakes ok:True.
- With the store reachable (mocked), handle() actually POSTs /v1/execute per
  block and returns the REAL block output, not a template.
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

import httpx
import pytest

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = ROOT / "blueprints" / "examples" / "basic_product.yaml"

_CANNED = "executed via Factory template"


def _generate_reuse_action(tmp_path: Path):
    """Generate a product and return (source_text, loaded_module) for the first
    REUSE action that references at least one block."""
    bp = load_blueprint(BLUEPRINT)
    out = tmp_path / "product"
    ProductGenerator(bp, factory_commit="t", blocks_commit="t").generate(out)

    actions_dir = out / "app" / "actions"
    for py in sorted(actions_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        text = py.read_text(encoding="utf-8")
        if 'STRATEGY = "REUSE"' in text and "BLOCK_IDS: List[str] = []" not in text:
            mod = types.ModuleType("generated_action_under_test")
            # The generated module imports app.cerebrum_product_kernel.* — the
            # SAME package the backend ships, so it resolves in-process. httpx
            # and os are real imports too.
            exec(compile(text, str(py), "exec"), mod.__dict__)  # noqa: S102
            return text, mod
    pytest.fail("no REUSE action with blocks was generated — test needs one")


def test_generated_action_source_is_not_a_canned_template(tmp_path):
    text, _ = _generate_reuse_action(tmp_path)
    assert _CANNED not in text, "generated action still returns the canned template string"
    assert "/v1/execute" in text, "generated action does not route to the store"
    assert "CEREBRUM_API_URL" in text, "generated action does not read the store URL"


def test_handle_degrades_honestly_when_store_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)
    _, mod = _generate_reuse_action(tmp_path)

    result = asyncio.run(mod.handle({"tenant_id": "t1"}, {"q": 1}))

    # Honest degradation — NOT a fake success.
    assert result["status"] == "dependency_required", result
    assert result.get("error_code") == "store_unconfigured", result
    # The canned success shape must be entirely absent.
    assert result.get("output") is None or "result" not in (result.get("output") or {})


def test_handle_invokes_store_and_returns_real_output(monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBRUM_API_URL", "https://store.example")
    monkeypatch.delenv("CEREBRUM_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_API_TOKEN", raising=False)
    _, mod = _generate_reuse_action(tmp_path)

    calls = []

    class _Resp:
        def __init__(self, block):
            self._block = block

        def raise_for_status(self):
            return None

        def json(self):
            return {"block_id": self._block, "result": {"real": True, "block": self._block}}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append((url, json))
            return _Resp(json["block"])

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = asyncio.run(mod.handle({"tenant_id": "t1"}, {"q": 1}))

    assert result["status"] == "success", result
    # The store was actually hit, once per block, at the right endpoint.
    assert calls, "the store was never called"
    assert all(url.endswith("/v1/execute") for url, _ in calls), calls
    assert [c[1]["block"] for c in calls] == list(mod.BLOCK_IDS), calls
    # Output carries REAL per-block results, not a template summary.
    block_results = result["output"]["result"]["block_results"]
    assert set(block_results) == set(mod.BLOCK_IDS)
    assert all(block_results[b]["result"]["real"] is True for b in mod.BLOCK_IDS)
    assert _CANNED not in str(result)
