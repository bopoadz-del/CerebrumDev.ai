"""Generate/Continue WRITER hook: keys present → run_cli_pivot, not dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.cli_pivot import (
    CLASS_INFRA,
    CURSOR_KEY_ENVS,
    EXECUTOR_UNAVAILABLE,
    ExecutorLaunch,
    ExecutorUnavailable,
    resolve_pivot_session_env,
    writer_uses_cli_pivot,
)
from app.factory.build.cli_receipt import HANDOFF_TO_N3
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import RoleContext, RoleError, RoleResult, run_writer
from app.factory.build.runner import Outcome, RoleRunner, blueprint_hash
from app.factory.build.workspace import RoleWorkspace
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
IDS = ["analytics_surface", "dashboard_surface"]


def _bp():
    return load_blueprint(SMOKE)


def _ctx(tmp_path: Path, *, state=None):
    bp = _bp()
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=bp,
        plan=plan_blueprint(bp),
        state=dict(state or {}),
    )


def _handoff_launch(**_k):
    return ExecutorLaunch(
        started=True,
        receipt={"schema": "cli_receipt.v1", "cli_authored_ids": IDS},
        changed_paths=[f"app/actions/{cid}.py" for cid in IDS],
    )


def test_writer_uses_cli_pivot_is_keys_present_only():
    assert writer_uses_cli_pivot({}) is False
    assert writer_uses_cli_pivot({"FACTORY_CLI_PIVOT": "1"}) is False
    assert writer_uses_cli_pivot({"CURSOR_API_KEY": "k"}) is True


def test_resolve_pivot_session_env_uses_factory_session_path(tmp_path):
    dest = tmp_path / "factory_outputs" / "sessions" / "sess_stable" / "demo"
    dest.mkdir(parents=True)
    env = resolve_pivot_session_env(dest, env={"CURSOR_API_KEY": "k"})
    assert env["FACTORY_SESSION_ID"] == "sess_stable"
    pinned = resolve_pivot_session_env(
        dest,
        env={"CURSOR_API_KEY": "k", "FACTORY_SESSION_ID": "already"},
    )
    assert pinned["FACTORY_SESSION_ID"] == "already"


def test_keys_absent_writer_does_not_call_cli_pivot(tmp_path, monkeypatch):
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    called = []

    def boom(*_a, **_k):
        called.append(True)
        raise AssertionError("run_writer_via_cli_pivot must not run without keys")

    monkeypatch.setattr(
        "app.factory.build.cli_pivot.run_writer_via_cli_pivot", boom
    )
    result = run_writer(_ctx(tmp_path))
    assert result.ok
    assert called == []
    assert "cli_pivot" not in (result.notes or {})


def test_keys_present_stub_launch_skips_dispatch(tmp_path, monkeypatch):
    dispatched = []

    def fake_dispatch(*_a, **_k):
        dispatched.append(True)
        raise AssertionError("dispatch_compiled_brief must not run on cli-pivot")

    monkeypatch.setattr(
        "app.factory.build.coder_session.dispatch_compiled_brief",
        fake_dispatch,
    )
    env = {"CURSOR_API_KEY": "cursor-test-key"}
    result = run_writer(_ctx(tmp_path), launch=_handoff_launch, env=env)
    assert dispatched == []
    assert result.ok
    assert result.notes["cli_pivot"]["honesty"] == HANDOFF_TO_N3
    assert result.notes["cli_pivot"]["green"] is False
    assert result.notes["next"] == "n3_gate"
    brief = (tmp_path / "build" / "docs" / "coder_brief.md").read_text(encoding="utf-8")
    assert "analytics_surface" in brief


def test_keys_present_launch_unavailable_is_infra(tmp_path):
    def boom(**_k):
        raise ExecutorUnavailable(f"{EXECUTOR_UNAVAILABLE}: Cursor API down")

    with pytest.raises(RoleError, match=EXECUTOR_UNAVAILABLE) as excinfo:
        run_writer(
            _ctx(tmp_path),
            launch=boom,
            env={"CURSOR_API_KEY": "cursor-test-key"},
        )
    assert CLASS_INFRA == "infra"
    assert EXECUTOR_UNAVAILABLE in str(excinfo.value)


def test_runner_handoff_is_not_product_green(tmp_path):
    bp = _bp()
    out = tmp_path / "build"
    out.mkdir()
    digest = blueprint_hash(bp)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=digest)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(
            EventKind.GATE_PASSED,
            role=role,
            detail="ok",
            payload={"gate": "seed"},
        )

    def handoff_writer(ctx):
        payload = {
            "honesty": HANDOFF_TO_N3,
            "next": "n3_gate",
            "green": False,
        }
        ctx.state["cli_pivot"] = payload
        return RoleResult(
            ok=True,
            detail="HANDOFF_TO_N3: store-gate next",
            notes={"cli_pivot": payload, "next": "n3_gate"},
        )

    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = handoff_writer
    runner = RoleRunner(bp, out, roles=roles, ledger=ledger)
    outcome = runner.run()
    assert outcome.outcome is Outcome.HANDOFF_TO_N3
    assert outcome.ok is False
    assert runner.ledger.succeeded() is False
    assert BuildRole.TESTER not in outcome.completed
    terminal = runner.ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert terminal.payload.get("next") == "n3_gate"
    assert terminal.payload.get("green") is False


def _require_kimi_cli(monkeypatch, tmp_path: Path) -> Path:
    """Kimi binary on PATH, no credentials, brief requires FACTORY_CODE_CLI."""
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    return script


def test_keys_absent_raise_if_cli_session_unready_still_demands_kimi(
    tmp_path, monkeypatch
):
    from app.factory.build.coder_session import (
        NAMED_BLOCKER_CLI_CREDS,
        CodeCliCredentialsMissing,
        brief_requires_cli,
        raise_if_cli_session_unready,
    )
    from app.factory.build_jobs import start_runner_build

    _require_kimi_cli(monkeypatch, tmp_path)
    assert brief_requires_cli() is True
    with pytest.raises(CodeCliCredentialsMissing, match=NAMED_BLOCKER_CLI_CREDS):
        raise_if_cli_session_unready()
    with pytest.raises(CodeCliCredentialsMissing, match=NAMED_BLOCKER_CLI_CREDS):
        start_runner_build(_bp(), tmp_path / "out")
    assert not (tmp_path / "out" / "build_ledger.jsonl").exists()


def test_cursor_keys_skip_kimi_preflight_so_generate_can_start(
    tmp_path, monkeypatch
):
    from app.factory.build.coder_session import (
        brief_requires_cli,
        raise_if_cli_session_unready,
    )
    from app.factory.build_jobs import start_runner_build

    _require_kimi_cli(monkeypatch, tmp_path)
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-key")
    assert writer_uses_cli_pivot() is True
    assert brief_requires_cli() is False
    raise_if_cli_session_unready()

    started = []

    def _record_start(self):
        started.append(self.name)

    monkeypatch.setattr("threading.Thread.start", _record_start)
    result = start_runner_build(_bp(), tmp_path / "out")
    assert started, "generate-start must spawn the runner without Kimi creds"
    assert result["already_running"] is False
    assert result["engine"] == "runner"
    assert (tmp_path / "out" / "build_ledger.jsonl").exists()
