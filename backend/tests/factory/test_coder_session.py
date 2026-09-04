"""One-session FACTORY_CODE_CLI dispatch + owner Pause/Stop."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    CONTROL_PAUSE,
    CONTROL_STOP,
    NAMED_BLOCKER_CLI,
    NAMED_BLOCKER_STOPPED,
    brief_dispatch_enabled,
    cli_available,
    dispatch_compiled_brief,
    read_control,
    read_log_tail,
    session_status,
    wait_if_paused,
    write_control,
)
from app.factory.build.roles_models import RoleContext
from app.factory.build.workspace import RoleWorkspace


class _Cap:
    capability_id = "analytics_surface"
    block_ids = ("analytics",)
    strategy = "REUSE"
    notes = "agg"


class _Plan:
    capabilities = (_Cap(),)


class _Blueprint:
    product_name = "Smoke"
    product_id = "runner-smoke"
    vertical = "product"
    summary = "smoke"


def _ctx(tmp_path: Path) -> RoleContext:
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        state={},
    )


def test_brief_dispatch_defaults_on(monkeypatch):
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    assert brief_dispatch_enabled() is True
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    assert brief_dispatch_enabled() is False


def test_pause_stop_resume_control_file(tmp_path):
    root = tmp_path / "ws"
    write_control(root, "pause")
    assert read_control(root) == CONTROL_PAUSE
    write_control(root, "resume")
    assert read_control(root) == "run"
    write_control(root, "stop")
    assert read_control(root) == CONTROL_STOP


def test_wait_if_paused_returns_stop_on_deadline(tmp_path):
    root = tmp_path / "ws"
    write_control(root, "pause")
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    action = wait_if_paused(
        root,
        clock=now,
        sleep=lambda _s: clock.__setitem__("t", clock["t"] + 1),
        poll_s=0.01,
        deadline=0.5,
    )
    assert action == CONTROL_STOP


def test_dispatch_without_cli_names_the_blocker(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_CODE_CLI", "/definitely/not/a/cli")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_CLI
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI
    status = session_status(tmp_path / "build")
    assert status["coder_receipt"]["blocker"] == NAMED_BLOCKER_CLI


def test_cli_session_honours_owner_stop(tmp_path, monkeypatch):
    script = tmp_path / "fake_coder.sh"
    script.write_text(
        "#!/bin/sh\n"
        "echo starting\n"
        "sleep 30\n"
        "echo never\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    write_control(tmp_path / "build", "stop")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_STOPPED
    log = read_log_tail(tmp_path / "build")
    assert "STOP" in log or result.detail


def test_cli_available_sees_an_executable(tmp_path, monkeypatch):
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    assert cli_available() is True
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    assert cli_available() is False
