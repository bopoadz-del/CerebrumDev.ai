"""S4: persist-wrapper gone; capability HTTP stays on execute_action."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.kernel import (
    DELETED,
    EMITTER_ID,
    KEPT,
    WRAPPER_SYMBOL,
    KernelError,
    assert_no_persist_wrapper,
    canonical_fingerprint,
    evaluate_kernel,
    fingerprint_disagreements,
    persist_wrapper_findings,
    reject_lotdesk_persist_wrapper,
    reread_matches,
    wrapper_symbol_gone,
    write_reread_twin,
)
from app.factory.build.preflight import write_evidence
from app.factory.build.roles import _coder_route_body
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_persist_wrapper_symbol_is_gone():
    from app.factory.build import roles as roles_mod

    assert not hasattr(roles_mod, WRAPPER_SYMBOL)
    assert wrapper_symbol_gone() is True
    with pytest.raises(ImportError):
        from app.factory.build.roles import _ensure_route_persists_payload  # noqa: F401


def test_lotdesk_class_persist_wrapper_is_rejected():
    rewriter = (
        "def _ensure_route_persists_payload(body: str) -> str:\n"
        "    rewritten = re.sub(\n"
        r'        r"\bsave\(\s*(?!payload\s*\))([A-Za-z_][\w]*)\s*\)",'
        "\n"
        '        "save(payload)",\n'
        "        body,\n"
        "    )\n"
        "    return rewritten\n"
    )
    findings = persist_wrapper_findings(rewriter)
    assert findings
    with pytest.raises(KernelError, match="persist rewriter"):
        assert_no_persist_wrapper(rewriter)

    handle_then_save = (
        "    result = handle(payload)\n"
        "    record = save(result)\n"
    )
    assert persist_wrapper_findings(handle_then_save)
    with pytest.raises(KernelError, match="LotDesk-class"):
        assert_no_persist_wrapper(handle_then_save)

    kernel_owned = (
        "    result = await run_capability(CAPABILITY_ID, payload)\n"
        "    stored = save(payload)\n"
    )
    assert persist_wrapper_findings(kernel_owned) == []
    assert_no_persist_wrapper(kernel_owned)


def test_lotdesk_fixture_persist_wrapper_is_rejected():
    result = reject_lotdesk_persist_wrapper()
    assert result["ok"] is False
    assert result["findings"]
    assert result["lotdesk"] == "fixture only; not patched"
    assert any("handle()" in item or "LotDesk" in item for item in result["findings"])


def test_role_runner_capability_http_goes_through_execute_action(tmp_path: Path):
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()

    runtime = out / "app" / "cerebrum_product_kernel" / "contract" / "runtime.py"
    assert runtime.is_file()
    assert "def execute_action" in runtime.read_text(encoding="utf-8")

    bridge = (out / "app" / "kernel_bridge.py").read_text(encoding="utf-8")
    assert "execute_action" in bridge
    assert "run_capability" in bridge

    routes = (out / "app" / "routes.py").read_text(encoding="utf-8")
    assert "from app.kernel_bridge import run_capability" in routes
    assert "await run_capability" in routes
    assert WRAPPER_SYMBOL not in routes
    assert persist_wrapper_findings(routes) == []


def test_coder_route_body_stays_none_on_s4_path():
    assert _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_evaluate_kernel_and_reread(tmp_path: Path):
    result = evaluate_kernel()
    assert result["stage"] == "S4"
    assert result["emitter"] == EMITTER_ID
    assert result["PILOT_READY"] is False
    assert result["pass_criteria"]["persist_wrapper_symbol_gone"] is True
    assert result["pass_criteria"]["coder_route_body_is_None"] is True
    assert result["pass_criteria"]["execute_action_callable"] is True
    assert result["pass_criteria"]["templated_route_uses_execute_action"] is True
    assert result["pass_criteria"]["lotdesk_persist_wrapper_rejected"] is True
    assert result["ok"] is True
    assert result["verdict"] == "PASS"
    assert result["not_started"] == [
        "S5",
        "S6",
        "S7",
        "S8",
        "S9",
        "S10",
        "S11",
        "S12",
        "S13",
    ]
    assert result["deleted"] == list(DELETED)
    assert result["kept"] == list(KEPT)
    dest = tmp_path / "S4_kernel.json"
    write_evidence(dest, result)
    twin_path = write_reread_twin(dest, result)
    written = json.loads(dest.read_text(encoding="utf-8"))
    twin = json.loads(twin_path.read_text(encoding="utf-8"))
    assert twin["disagreements"] == []
    assert reread_matches(written, twin) is True
    assert canonical_fingerprint(result) == canonical_fingerprint(evaluate_kernel())
    other = evaluate_kernel()
    other["git_sha"] = "0" * 40
    assert "git_sha" in fingerprint_disagreements(result, other)
