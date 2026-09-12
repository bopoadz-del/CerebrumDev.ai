"""N1 keyless seam: compose C-BRIEF → executor → no author fallback."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.budget_inspect import CEILING_S
from app.factory.build.cli_pivot import (
    BUDGET_EXCEEDED,
    CLASS_INFRA,
    CURSOR_KEY_ENVS,
    DEFAULT_WALL_S,
    EXECUTOR_UNAVAILABLE,
    ExecutorLaunch,
    compose_cbrief,
    cursor_keys_present,
    decide_budget,
    run_cli_pivot,
)
from app.factory.build.cli_receipt import HANDOFF_TO_N3, RECEIPT_INVALID
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.cli import main
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
PIVOT_PY = ROOT / "backend/app/factory/build/cli_pivot.py"
RECEIPT_PY = ROOT / "backend/app/factory/build/cli_receipt.py"
CURSOR_BA_PY = ROOT / "backend/app/factory/build/cursor_ba.py"
BUILDS_PUSH_PY = ROOT / "backend/app/factory/build/builds_push.py"
GATE_YML = ROOT / "docs/pivot/cerebrum-builds-store-gate.yml"
N3_FLOOR = (
    "no_token_401",
    "missing_field_422",
    "enum_422",
    "ui_served_200",
    "rag_roundtrip_hit",
    "single_persistence_root",
    "ci_present_full_suite",
    "handler_bodies_distinct",
    "health_fail_closed",
    "openapi_committed",
    "docker_health_200",
    "authorship==receipt",
)


def _bp():
    return load_blueprint(SMOKE)


def _honesty(path: Path) -> list[str]:
    return [
        str((event.payload or {}).get("honesty") or "")
        for event in BuildLedger(path).events()
    ]


def test_audit_baseline_keeps_render_slot_bodies():
    text = (ROOT / "AUDIT_BASELINE.md").read_text(encoding="utf-8")
    assert "HEADLINE FINDING" in text
    assert "## E resolution" in text
    assert "**KEEP.**" in text
    assert "brief_compiler.py:422" in text
    assert "roles_handlers.py:1377" in text
    assert "_templated_body" in text
    assert "Option C Hybrid" in text
    assert "tests/**" in text
    assert "12/12 remains cheat-resistance" in text
    assert "blocks.lock.json" in text
    assert "build_ledger.jsonl" in text
    assert "sealed" in text
    assert "until N2" in text


def test_store_gate_scaffold_is_docker_not_render():
    text = GATE_YML.read_text(encoding="utf-8")
    readme = (ROOT / "docs/pivot/README.md").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "docker build" in text
    assert "scripts/acceptance.py" in text
    assert "store_gate.json" in text
    for name in N3_FLOOR:
        assert name in text
        assert name in readme
    assert "runs-on: ubuntu-latest" in text
    assert "Render worker" in text
    assert "no Docker daemon" in text
    assert "runs-on: render" not in text.lower()
    assert "Option C Hybrid" in readme
    assert "tests/**" in readme
    assert "12/12 remains cheat-resistance" in readme
    assert "until N2" in readme
    assert "N1a" in readme
    assert "CEREBRUM_BUILDS_GITHUB_TOKEN" in readme
    assert "still not green" in readme


def test_new_path_source_has_no_author_fallback():
    for path in (PIVOT_PY, RECEIPT_PY, CURSOR_BA_PY, BUILDS_PUSH_PY):
        src = path.read_text(encoding="utf-8")
        assert "_templated_body(" not in src
        assert "dispatch_compiled_brief(" not in src
        assert "generate_from_compiled_brief(" not in src
        assert "_extend_wall(" not in src


def test_decide_budget_before_dispatch_clamps_to_s07():
    budget = decide_budget(wall_s=10_000.0, spend_cap_usd=1.5)
    assert budget.wall_s == CEILING_S
    assert budget.spend_cap_usd == 1.5
    assert budget.to_dict()["extend_wall"] is False
    assert budget.to_dict()["decided_before_dispatch"] is True
    default = decide_budget()
    assert default.wall_s == DEFAULT_WALL_S
    assert default.wall_s <= CEILING_S


def test_compose_cbrief_is_deterministic_brief_text():
    bp = _bp()
    compiled = compose_cbrief(bp, plan=plan_blueprint(bp), budget_s=1800.0)
    assert "TARGET" in compiled.text
    assert "analytics_surface" in compiled.text
    assert "dashboard_surface" in compiled.text
    assert "def handle(" not in compiled.text
    assert compiled.budget_s == 1800.0


def test_keyless_launch_is_executor_unavailable(tmp_path):
    bp = _bp()
    out = tmp_path / "w"
    result = run_cli_pivot(bp, out, plan=plan_blueprint(bp), env={})
    assert result.honesty == EXECUTOR_UNAVAILABLE
    assert result.failure_class == CLASS_INFRA
    assert result.green is False
    assert result.next is None
    assert result.ok is False
    assert EXECUTOR_UNAVAILABLE in _honesty(out / "build_ledger.jsonl")
    kinds = {e.kind for e in BuildLedger(out / "build_ledger.jsonl").events()}
    assert EventKind.GATE_FAILED in kinds
    brief = (out / "docs" / "coder_brief.md").read_text(encoding="utf-8")
    assert "analytics_surface" in brief


def test_keys_present_still_no_live_call(tmp_path):
    """Keys present without a builds token is still infra — not the keyless prefix."""
    bp = _bp()
    env = {name: "not-a-live-key" for name in CURSOR_KEY_ENVS}
    assert cursor_keys_present(env) is True
    result = run_cli_pivot(bp, tmp_path / "w", plan=plan_blueprint(bp), env=env)
    assert result.honesty == EXECUTOR_UNAVAILABLE
    assert result.failure_class == CLASS_INFRA
    assert "keyless prefix" not in result.detail
    assert "CEREBRUM_BUILDS_GITHUB_TOKEN" in result.detail


def test_hung_or_never_started_is_infra(tmp_path):
    bp = _bp()
    plan = plan_blueprint(bp)

    hung = run_cli_pivot(
        bp,
        tmp_path / "hung",
        plan=plan,
        launch=lambda **_k: ExecutorLaunch(started=True, hung=True),
    )
    assert hung.honesty == EXECUTOR_UNAVAILABLE
    assert hung.failure_class == CLASS_INFRA
    assert "hung" in hung.detail

    idle = run_cli_pivot(
        bp,
        tmp_path / "idle",
        plan=plan,
        launch=lambda **_k: ExecutorLaunch(started=False),
    )
    assert idle.honesty == EXECUTOR_UNAVAILABLE
    assert "never started" in idle.detail


def test_budget_exceeded_before_and_after_dispatch(tmp_path):
    bp = _bp()
    plan = plan_blueprint(bp)
    before = run_cli_pivot(
        bp,
        tmp_path / "before",
        plan=plan,
        wall_s=10.0,
        spend_cap_usd=0.0,
        elapsed_s=11.0,
    )
    assert before.honesty == BUDGET_EXCEEDED
    assert before.failure_class == CLASS_INFRA
    assert BUDGET_EXCEEDED in _honesty(tmp_path / "before" / "build_ledger.jsonl")

    after = run_cli_pivot(
        bp,
        tmp_path / "after",
        plan=plan,
        wall_s=10.0,
        spend_cap_usd=0.0,
        launch=lambda **_k: ExecutorLaunch(
            started=True,
            elapsed_s=11.0,
            spent_usd=0.0,
            receipt={"cli_authored_ids": ["analytics_surface", "dashboard_surface"]},
            changed_paths=[
                "app/actions/analytics_surface.py",
                "app/actions/dashboard_surface.py",
            ],
        ),
    )
    assert after.honesty == BUDGET_EXCEEDED
    assert after.failure_class == CLASS_INFRA


def test_cli_pivot_command_fail_closes_keyless(tmp_path, monkeypatch):
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    out = tmp_path / "cli"
    rc = main(
        [
            "cli-pivot",
            "--blueprint",
            str(SMOKE),
            "--out",
            str(out),
            "--wall-s",
            "1800",
        ]
    )
    assert rc == 1
    payload = json.loads((out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["payload"]["honesty"] == EXECUTOR_UNAVAILABLE


def test_keys_present_stub_launch_unchanged(tmp_path):
    """Injected launch= still short-circuits even when live keys + token exist."""
    bp = _bp()
    ids = ["analytics_surface", "dashboard_surface"]
    env = {name: "not-a-live-key" for name in CURSOR_KEY_ENVS}
    env["CEREBRUM_BUILDS_GITHUB_TOKEN"] = "also-not-live"
    result = run_cli_pivot(
        bp,
        tmp_path / "stub",
        plan=plan_blueprint(bp),
        env=env,
        launch=lambda **_k: ExecutorLaunch(
            started=True,
            receipt={"schema": "cli_receipt.v1", "cli_authored_ids": ids},
            changed_paths=[f"app/actions/{cid}.py" for cid in ids],
        ),
    )
    assert result.honesty == HANDOFF_TO_N3
    assert result.green is False
    assert result.next == "n3_gate"


def test_happy_path_stub_hands_to_n3_not_green(tmp_path):
    bp = _bp()
    ids = ["analytics_surface", "dashboard_surface"]
    result = run_cli_pivot(
        bp,
        tmp_path / "ok",
        plan=plan_blueprint(bp),
        launch=lambda **_k: ExecutorLaunch(
            started=True,
            receipt={"schema": "cli_receipt.v1", "cli_authored_ids": ids},
            changed_paths=[f"app/actions/{cid}.py" for cid in ids],
        ),
    )
    assert result.honesty == HANDOFF_TO_N3
    assert result.green is False
    assert result.next == "n3_gate"
    assert result.ok is True
    assert result.cli_authored_ids == ids
    events = BuildLedger(tmp_path / "ok" / "build_ledger.jsonl").events()
    handoff = [e for e in events if (e.payload or {}).get("honesty") == HANDOFF_TO_N3]
    assert handoff
    assert handoff[-1].kind == EventKind.NOTE
    assert handoff[-1].payload.get("green") is False


def test_partial_credit_receipt_is_invalid(tmp_path):
    bp = _bp()
    result = run_cli_pivot(
        bp,
        tmp_path / "partial",
        plan=plan_blueprint(bp),
        launch=lambda **_k: ExecutorLaunch(
            started=True,
            receipt={"cli_authored_ids": ["analytics_surface"]},
            changed_paths=["app/actions/analytics_surface.py"],
        ),
    )
    assert result.honesty == RECEIPT_INVALID
    assert result.failure_class == "content"
    assert "missing=dashboard_surface" in result.detail
