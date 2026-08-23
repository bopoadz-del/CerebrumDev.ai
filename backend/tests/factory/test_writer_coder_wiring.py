"""The WRITER asks the coding agent, and never lies about who wrote the code.

New-shape tests for wiring the coder into the runner's WRITER role. The hazard
is authorship drift: a handler header that credits an LLM for a template, or a
coder failure that disappears so the build looks like the agent worked when it
did not. Both are the "plausible green" family — the artifact looks like
manufactured output either way, and only the provenance distinguishes them.

No LLM key and no network: the coder entry point is monkeypatched, which is
also what lets CI exercise this path at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


@pytest.fixture(autouse=True)
def _coder_on(monkeypatch):
    """Coder enabled, but every entry point stubbed — no paid calls, ever.

    The WRITER now asks the coder for four artifact classes, not one. Stubbing
    only generate_platform_handler left the model-spec, route-body and README
    calls hitting the live API on any machine with a key: the suite went past
    two minutes and started costing money. Each test below overrides the one
    entry point it is actually about.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr(
        "app.factory.coder.generate_model_spec",
        lambda **kw: {
            "entity": kw["capability_id"].replace("-", "_"),
            "fields": [{"name": "reference", "type": "str", "required": True}],
            "model": "stub-spec",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_route_body",
        lambda **kw: {"body": _STUB_ROUTE, "model": "stub-route"},
    )
    # The README path calls _llm_code_call directly.
    monkeypatch.setattr(
        "app.factory.coder._llm_code_call", lambda messages: "# stub readme\n"
    )
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: {
            "reviews": [
                {
                    "capability_id": c.get("id"),
                    "block_ids": c.get("block_ids") or [],
                    "verdict": "endorse",
                    "reason": "stub",
                }
                for c in kw.get("capabilities") or []
            ],
            "model": "stub-collector",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.propose_domain_test_cases",
        lambda **kw: {"cases": [], "model": "stub-tester"},
    )


_STUB_ROUTE = (
    "    result = handle(payload)\n"
    '    return {"ok": True, "capability": CAPABILITY_ID, "result": result}'
)


def _headers(out: Path) -> dict:
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in (out / "app" / "actions").glob("*.py")
        if p.stem != "__init__"
    }


def test_the_writer_uses_the_coding_agent_when_one_is_configured(
    blueprint, tmp_path, monkeypatch
):
    calls = []

    def fake_coder(**kwargs):
        calls.append(kwargs)
        return {
            "body": '    return {"capability": CAPABILITY_ID, "agent": True}',
            "model": "test-model-1",
        }

    monkeypatch.setattr("app.factory.coder.generate_platform_handler", fake_coder)

    out = tmp_path / "build"
    outcome = RoleRunner(blueprint, out).run()
    assert outcome.ok, outcome.to_dict()

    assert len(calls) == 2, "one coder call per capability"
    assert {c["capability_id"] for c in calls} == {
        "analytics_surface",
        "dashboard_surface",
    }
    # The coder is told which blocks it may drive.
    assert all("analytics" in c["block_ids"] or "dashboard" in c["block_ids"] for c in calls)

    for name, text in _headers(out).items():
        assert "coder LLM (test-model-1)" in text, name
        assert '"agent": True' in text, name


def test_a_coder_failure_ships_the_template_and_records_why(
    blueprint, tmp_path, monkeypatch
):
    """Degraded output is acceptable; invisible degradation is not."""
    from app.factory.coder import CoderError

    def failing_coder(**kwargs):
        raise CoderError("model refused")

    monkeypatch.setattr("app.factory.coder.generate_platform_handler", failing_coder)

    out = tmp_path / "build"
    runner = RoleRunner(blueprint, out)
    outcome = runner.run()

    assert outcome.ok, "a coder failure must not fail the build"
    for name, text in _headers(out).items():
        assert "deterministic contract template" in text, name
        assert "coder LLM" not in text, name

    failures = runner.state.get("coder_failures", {})
    assert set(failures) == {"analytics_surface", "dashboard_surface"}
    assert "model refused" in failures["analytics_surface"]

    # Only the handler coder failed, so only the handlers fall back. The other
    # artifact classes have their own coder calls and are unaffected -- the
    # accounting must be per-artifact, not a single global verdict.
    sources = runner.state["artifact_sources"]
    for cap_id in ("analytics_surface", "dashboard_surface"):
        assert sources[cap_id] == "deterministic contract template", cap_id
        assert sources[f"model:{cap_id}"].startswith("coder LLM")
        # Routes are kernel-owned (U12). An LLM body would bypass execute_action.
        assert sources[f"route:{cap_id}"] == "kernel execute_action template"


def test_the_coder_is_not_called_when_disabled(blueprint, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    called = []
    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler",
        lambda **kw: called.append(kw) or {"body": "    return {}", "model": "x"},
    )

    out = tmp_path / "build"
    assert RoleRunner(blueprint, out).run().ok
    assert called == []
    for text in _headers(out).values():
        assert "deterministic contract template" in text


def test_rework_findings_are_handed_to_the_coder(blueprint, tmp_path, monkeypatch):
    """A second attempt must be told what failed, not guess from scratch."""
    seen = []

    def fake_coder(**kwargs):
        seen.append(list(kwargs.get("work_list") or []))
        return {"body": '    return {"capability": CAPABILITY_ID}', "model": "m"}

    monkeypatch.setattr("app.factory.coder.generate_platform_handler", fake_coder)

    from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult

    def barren_tester(ctx):
        return RoleResult(ok=True, detail="wrote no tests")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = barren_tester

    from app.factory.build.runner import BuildBudget

    runner = RoleRunner(
        blueprint, tmp_path / "build", roles=roles, budget=BuildBudget(max_rework=1)
    )
    runner.run()

    # First writer pass has no findings; the rework pass carries the gate's.
    assert seen[0] == []
    rework_lists = [w for w in seen if w]
    assert rework_lists, "the rework pass must receive the tester's findings"
    assert any("tester produced no test files" in item for item in rework_lists[0])


def test_coder_output_still_passes_the_validation_gate(monkeypatch):
    """The agent does not get to bypass the static gate on emitted code."""
    import app.factory.coder as coder

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "sk-not-real")
    # Column-zero statements: _validate_body indents them into the wrapper.
    monkeypatch.setattr(
        coder, "_llm_code_call", lambda messages: "import os\nreturn {}"
    )

    with pytest.raises(coder.CoderError, match="forbidden construct"):
        coder.generate_platform_handler(
            capability_id="c",
            description="d",
            block_ids=["analytics"],
            product_name="P",
            vertical="v",
        )


def test_valid_coder_output_is_indented_into_the_handler(monkeypatch):
    """The body the WRITER writes must be function-body indented, once."""
    import app.factory.coder as coder

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "sk-not-real")
    monkeypatch.setattr(
        coder,
        "_llm_code_call",
        lambda messages: 'data = execute(BLOCK_IDS[0], payload)\nreturn {"capability": CAPABILITY_ID, "data": data}',
    )

    result = coder.generate_platform_handler(
        capability_id="c",
        description="d",
        block_ids=["analytics"],
        product_name="P",
        vertical="v",
    )
    body = result["body"]
    assert body.startswith("    data = execute(")
    assert not body.startswith("        "), "double-indented body would not compile"
    # And it compiles in the shape roles._handler_module places it in.
    compile(f"def handle(payload):\n{body}\n", "<t>", "exec")
