"""One-session FACTORY_CODE_CLI dispatch + owner Pause/Stop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    CONTROL_PAUSE,
    CONTROL_STOP,
    DEFAULT_KIMI_CODE_MODEL,
    NAMED_BLOCKER_CLI,
    NAMED_BLOCKER_CLI_CREDS,
    NAMED_BLOCKER_CLI_FAILED,
    NAMED_BLOCKER_CLI_NO_MODEL,
    NAMED_BLOCKER_STOPPED,
    CodeCliCredentialsMissing,
    CodeCliNoModelConfigured,
    CodeCliUnavailable,
    DispatchResult,
    _has_brief_workflow_steps,
    _is_keepable_handler,
    _merge_workspace_harvest,
    brief_dispatch_enabled,
    brief_requires_cli,
    classify_cli_exit,
    cli_available,
    cli_credentials_ok,
    cli_unavailable_detail,
    dispatch_compiled_brief,
    ensure_code_cli_credentials,
    harvest_cli_artifacts,
    http_oneshot_enabled,
    probe_code_cli,
    raise_if_cli_session_unready,
    read_control,
    read_log_tail,
    resolve_code_cli,
    session_status,
    specs_from_models_source,
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
    """CLI missing + coder on → named class; HTTP oneshot is not used."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", "/definitely/not/a/cli")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "unavailable"
    assert result.blocker == NAMED_BLOCKER_CLI
    assert NAMED_BLOCKER_CLI in result.detail
    assert "FACTORY_CODE_CLI" in result.detail
    assert oneshot == [], "HTTP oneshot must not run when CLI is missing"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI
    status = session_status(tmp_path / "build")
    assert status["coder_receipt"]["blocker"] == NAMED_BLOCKER_CLI
    log = read_log_tail(tmp_path / "build")
    assert NAMED_BLOCKER_CLI in log
    assert "falling back to HTTP oneshot" not in log


def test_dispatch_with_cli_does_not_use_oneshot(tmp_path, monkeypatch):
    script = tmp_path / "fake_coder.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert oneshot == [], "CLI present must not call HTTP oneshot"


def test_brief_requires_cli_when_coder_on(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    assert brief_requires_cli() is True
    assert http_oneshot_enabled() is False
    monkeypatch.setenv("FACTORY_BRIEF_HTTP_ONESHOT", "1")
    assert brief_requires_cli() is False
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    assert brief_requires_cli() is False
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    monkeypatch.setenv("ENV", "test")
    assert brief_requires_cli() is False
    monkeypatch.setenv("ENV", "production")
    assert brief_requires_cli() is True


def test_cli_unavailable_detail_names_env():
    text = cli_unavailable_detail("/no/such/coder")
    assert NAMED_BLOCKER_CLI in text
    assert "FACTORY_CODE_CLI" in text
    assert "KIMI_CODE_API_KEY" in text
    assert "FACTORY_BRIEF_HTTP_ONESHOT" in text


def test_probe_code_cli_reports_unavailable(monkeypatch):
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    probe = probe_code_cli()
    assert probe["available"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI
    assert probe["requires_cli"] is True


def test_resolve_code_cli_finds_home_local_bin(tmp_path, monkeypatch):
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    script = bindir / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FACTORY_CODE_CLI", "kimi")
    monkeypatch.delenv("KIMI_CODE_CLI", raising=False)
    # Not on PATH — only the extra home location.
    monkeypatch.setenv("PATH", "/usr/bin")
    assert resolve_code_cli() == str(script)
    assert cli_available() is True


def test_ensure_code_cli_credentials_writes_when_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    result = ensure_code_cli_credentials()
    assert result["wrote"] is True
    assert result.get("mutated") is False
    assert result["model"] == DEFAULT_KIMI_CODE_MODEL
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "[providers.kimi]" in text
    assert "sk-test-not-real" in text
    assert f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"' in text
    assert f'[models."{DEFAULT_KIMI_CODE_MODEL}"]' in text
    assert 'model = "k3"' in text
    assert "max_context_size = 1048576" in text


def test_ensure_code_cli_credentials_honours_kimi_code_model(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("KIMI_CODE_MODEL", "kimi-code/kimi-for-coding")
    result = ensure_code_cli_credentials()
    assert result["wrote"] is True
    assert result["model"] == "kimi-code/kimi-for-coding"
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert 'default_model = "kimi-code/kimi-for-coding"' in text
    assert '[models."kimi-code/kimi-for-coding"]' in text
    assert 'model = "kimi-for-coding"' in text


def test_ensure_code_cli_credentials_mutates_when_model_missing(tmp_path, monkeypatch):
    """Live-shaped file: [providers.kimi] present, default_model absent."""
    home = tmp_path / "kimi-home"
    home.mkdir()
    dest = home / "config.toml"
    dest.write_text(
        "[providers.kimi]\n"
        'type = "kimi"\n'
        'api_key = "sk-test-not-real"\n'
        'base_url = "https://api.moonshot.ai/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["wrote"] is False
    assert result["mutated"] is True
    assert result["model"] == DEFAULT_KIMI_CODE_MODEL
    text = dest.read_text(encoding="utf-8")
    assert f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"' in text
    assert f'[models."{DEFAULT_KIMI_CODE_MODEL}"]' in text
    assert "[providers.kimi]" in text
    assert "sk-test-not-real" in text
    again = ensure_code_cli_credentials()
    assert again["wrote"] is False
    assert again["mutated"] is False
    assert again["reason"] == "already present"


def test_ensure_code_cli_credentials_skips_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    result = ensure_code_cli_credentials()
    assert result["wrote"] is False
    assert "unset" in result["reason"]


def test_classify_cli_exit_names_no_model_configured():
    blocker, detail = classify_cli_exit(
        1,
        "error: failed to run prompt: No model configured. "
        "Run 'kimi' and use /login to sign in, then retry; "
        "or set default model in config.toml.",
    )
    assert blocker == NAMED_BLOCKER_CLI_NO_MODEL
    assert CodeCliNoModelConfigured.blocker == NAMED_BLOCKER_CLI_NO_MODEL
    assert "No model configured" in detail
    assert "KIMI_CODE_MODEL" in detail
    assert DEFAULT_KIMI_CODE_MODEL in detail
    assert "templated" in detail
    generic, generic_detail = classify_cli_exit(1, "segfault")
    assert generic == NAMED_BLOCKER_CLI_FAILED
    assert generic_detail == "CLI exited 1"


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


def test_specs_from_models_source_reads_fields_and_constraints():
    source = (
        "class Appointment:\n"
        "    FIELDS = ['reference', 'status', 'pet_name']\n"
        "    CONSTRAINTS = {'status': {'allowed_values': ['open', 'closed']}}\n"
        "    ENTITY = 'appointment'\n"
        "\n"
        "MODELS = {'appointment_scheduling': Appointment}\n"
    )
    specs = specs_from_models_source(source)
    assert "appointment_scheduling" in specs
    names = [f["name"] for f in specs["appointment_scheduling"]["fields"]]
    assert names == ["reference", "status", "pet_name"]
    status = next(
        f for f in specs["appointment_scheduling"]["fields"] if f["name"] == "status"
    )
    assert status["allowed_values"] == ["open", "closed"]


_PREPARED_EVENT_BUS_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{\n"
    "        'block': 'event_bus',\n"
    "        'action': 'publish',\n"
    "        'input': {\n"
    "            'topic': 'reminder.due',\n"
    "            'payload': {'reference': payload.get('reference')},\n"
    "            'message': 'reminder recorded',\n"
    "            'channel': 'mcp',\n"
    "        },\n"
    "    }]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

_UNPREPARED_EVENT_BUS_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{'block': 'event_bus', 'input': payload}]\n"
    "    return execute('workflow', {'steps': steps})\n"
)


def test_harvest_keeps_brief_driven_workflow_steps_without_capability_id(tmp_path):
    """#317 keepable required CAPABILITY_ID; CLI workflow modules often omit it.

    After #318 the keep-path treated unprepared ``input: payload`` as
    brief-driven. Only the prepared PRODUCT contract is keepable.
    """
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "reminders_and_notifications.py").write_text(
        _PREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    (actions / "clinic_intake.py").write_text(
        "# fragment — not a keepable handler\nreturn {}\n",
        encoding="utf-8",
    )
    assert _has_brief_workflow_steps(
        (actions / "reminders_and_notifications.py").read_text(encoding="utf-8")
    )
    assert _is_keepable_handler(
        (actions / "reminders_and_notifications.py").read_text(encoding="utf-8")
    )
    specs, kept = harvest_cli_artifacts(
        root, ["reminders_and_notifications", "clinic_intake"]
    )
    assert kept == ["reminders_and_notifications"]
    assert specs == {}


def test_harvest_does_not_keep_unprepared_event_bus_forward(tmp_path):
    """Live sess_a4690fb: CLI wrote {'block': 'event_bus', 'input': payload}."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        "CAPABILITY_ID = 'appointment_scheduling'\n" + _UNPREPARED_EVENT_BUS_HANDLER,
        encoding="utf-8",
    )
    (actions / "reminders_notifications.py").write_text(
        _UNPREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    assert _is_keepable_handler(
        (actions / "appointment_scheduling.py").read_text(encoding="utf-8")
    ) is False
    specs, kept = harvest_cli_artifacts(
        root, ["appointment_scheduling", "reminders_notifications"]
    )
    assert kept == []
    assert specs == {}


_MIXED_STEP2_EVENT_BUS_HANDLER = (
    "def handle(payload):\n"
    "    steps = [\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.booked',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'booked',\n"
    "                'channel': 'mcp',\n"
    "            },\n"
    "        },\n"
    "        {'block': 'event_bus', 'input': payload},\n"
    "    ]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)


def test_harvest_does_not_keep_prepared_step1_raw_step2(tmp_path):
    """Live sess_d70c18ef: appointment_booking step_2 still forwarded payload."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(
        "CAPABILITY_ID = 'appointment_booking'\n" + _MIXED_STEP2_EVENT_BUS_HANDLER,
        encoding="utf-8",
    )
    assert _is_keepable_handler(
        (actions / "appointment_booking.py").read_text(encoding="utf-8")
    ) is False
    specs, kept = harvest_cli_artifacts(root, ["appointment_booking"])
    assert kept == []
    assert specs == {}


def test_harvest_cli_body_does_not_overwrite_workspace_workflow_steps(tmp_path):
    """Same-session CLI: thin JSON body must not replace brief-driven steps."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "reminders_and_notifications.py").write_text(
        _PREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="cli",
        handlers={
            "reminders_and_notifications": (
                "return {'ok': True, 'capability': 'reminders_and_notifications'}"
            )
        },
    )
    _merge_workspace_harvest(
        result, root, ["reminders_and_notifications"]
    )
    assert "reminders_and_notifications" in result.kept_handler_ids
    assert "reminders_and_notifications" not in result.handlers


def test_harvest_cli_does_not_prefer_unprepared_disk_over_json_body(tmp_path):
    """Unprepared disk must not lock in; factory wrap should get the body."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        _UNPREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    body = "return {'ok': True, 'capability': 'appointment_scheduling'}"
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="cli",
        handlers={"appointment_scheduling": body},
    )
    _merge_workspace_harvest(result, root, ["appointment_scheduling"])
    assert result.handlers["appointment_scheduling"] == body
    assert "appointment_scheduling" not in result.kept_handler_ids


def test_harvest_cli_does_not_prefer_mixed_step2_disk(tmp_path):
    """Prepared step_1 + raw step_2 must not pin appointment_booking."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(
        _MIXED_STEP2_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    body = "return {'ok': True, 'capability': 'appointment_booking'}"
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="cli",
        handlers={"appointment_booking": body},
    )
    _merge_workspace_harvest(result, root, ["appointment_booking"])
    assert result.handlers["appointment_booking"] == body
    assert "appointment_booking" not in result.kept_handler_ids


def test_harvest_oneshot_does_not_pin_a_previous_round_handler(tmp_path):
    """Rework oneshot must keep its body so a red PRODUCT handler can change."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "reminders_and_notifications.py").write_text(
        _UNPREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    body = "return {'ok': True, 'capability': 'reminders_and_notifications'}"
    result = DispatchResult(
        via="http_oneshot",
        ok=True,
        detail="oneshot",
        handlers={"reminders_and_notifications": body},
    )
    _merge_workspace_harvest(
        result, root, ["reminders_and_notifications"]
    )
    assert result.handlers["reminders_and_notifications"] == body
    assert "reminders_and_notifications" not in result.kept_handler_ids


def test_harvest_cli_artifacts_keeps_handle_modules(tmp_path):
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (root / "app" / "models.py").write_text(
        "class Reminder:\n"
        "    FIELDS = ['reference', 'status']\n"
        "    CONSTRAINTS = {}\n"
        "MODELS = {'automated_reminders': Reminder}\n",
        encoding="utf-8",
    )
    (actions / "automated_reminders.py").write_text(
        'CAPABILITY_ID = "automated_reminders"\n'
        "def handle(payload):\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n",
        encoding="utf-8",
    )
    (actions / "clinic_intake.py").write_text(
        "# fragment — not a keepable handler\nreturn {}\n",
        encoding="utf-8",
    )
    specs, kept = harvest_cli_artifacts(
        root, ["automated_reminders", "clinic_intake"]
    )
    assert "automated_reminders" in specs
    assert kept == ["automated_reminders"]


def test_cli_dispatch_harvests_workspace_specs_and_handlers(tmp_path, monkeypatch):
    script = tmp_path / "fake_coder.sh"
    dest = tmp_path / "build"
    script.write_text(
        "#!/bin/sh\n"
        "mkdir -p app/actions\n"
        "cat > app/models.py << 'EOF'\n"
        "class Appointment:\n"
        "    FIELDS = ['reference', 'status', 'pet_name']\n"
        "    CONSTRAINTS = {'status': {'allowed_values': ['open', 'closed']}}\n"
        "MODELS = {'analytics_surface': Appointment}\n"
        "EOF\n"
        "cat > app/actions/analytics_surface.py << 'EOF'\n"
        'CAPABILITY_ID = "analytics_surface"\n'
        "def handle(payload):\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n"
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert "analytics_surface" in result.specs
    names = [f["name"] for f in result.specs["analytics_surface"]["fields"]]
    assert "pet_name" in names
    assert result.kept_handler_ids == ["analytics_surface"]
    receipt = json.loads(
        (dest / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["kept_handler_ids"] == ["analytics_surface"]


def test_start_runner_build_refuses_when_cli_missing(tmp_path, monkeypatch):
    from app.factory.build_jobs import start_runner_build
    from app.factory.blueprint import load_blueprint

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    with pytest.raises(CodeCliUnavailable, match=NAMED_BLOCKER_CLI):
        start_runner_build(bp, tmp_path / "out")
    assert not (tmp_path / "out" / "build_ledger.jsonl").exists()


def test_approve_and_generate_names_cli_class_without_takeover(tmp_path, monkeypatch):
    from app.factory import platform_chat_flow
    from app.models.session import ProductDesignState, SessionState

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    root = Path(__file__).resolve().parents[3]
    from app.factory.blueprint import load_blueprint

    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    state = SessionState(
        session_id="sess_cli_missing",
        user_id="test-user",
        product_design=ProductDesignState(
            blueprint=bp.model_dump(mode="json"),
            blueprint_approved=False,
        ),
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is False
    assert result["sse"] == "error"
    assert result["blocker"] == NAMED_BLOCKER_CLI
    assert NAMED_BLOCKER_CLI in result["summary"]
    assert "never opened" in result["summary"]
    assert state.product_design.generation is None
    assert NAMED_BLOCKER_CLI in (state.product_design.last_error or "")


def test_cli_available_sees_an_executable(tmp_path, monkeypatch):
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    assert cli_available() is True
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    assert cli_available() is False


def _fake_kimi(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _require_cli(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)


def test_dispatch_kimi_without_config_toml_fail_closed(tmp_path, monkeypatch):
    """Binary on PATH + no config.toml must not open a WRITER CLI session."""
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ran = []
    monkeypatch.setattr(
        "app.factory.build.coder_session._run_cli_session",
        lambda *a, **kw: ran.append(True) or DispatchResult(via="cli", ok=True, detail="nope"),
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "unavailable"
    assert result.blocker == NAMED_BLOCKER_CLI_CREDS
    assert NAMED_BLOCKER_CLI_CREDS in result.detail
    assert "credentials_file_present=false" in result.detail
    assert oneshot == []
    assert ran == [], "missing credentials must not start FACTORY_CODE_CLI"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_CREDS
    log = read_log_tail(tmp_path / "build")
    assert NAMED_BLOCKER_CLI_CREDS in log
    assert "falling back to HTTP oneshot" not in log


def test_dispatch_cli_no_model_configured_fail_closed(tmp_path, monkeypatch):
    """CLI exit with 'No model configured' is a named fail-closed class."""
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo \"error: failed to run prompt: No model configured. "
        "Run 'kimi' and use /login to sign in, then retry; "
        "or set default model in config.toml.\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(
        "[providers.kimi]\ntype = \"kimi\"\napi_key = \"sk-test-not-real\"\n",
        encoding="utf-8",
    )
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_NO_MODEL
    assert "No model configured" in result.detail
    assert "templated" in result.detail
    assert oneshot == [], "CLI no-model must not fall back to HTTP oneshot"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_NO_MODEL
    failures = ctx.state.get("coder_failures") or {}
    assert NAMED_BLOCKER_CLI_NO_MODEL in failures.get("brief_dispatch", "")


def test_dispatch_cli_other_nonzero_stays_failed(tmp_path, monkeypatch):
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\necho boom\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text("[providers.kimi]\n", encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_CLI_FAILED
    assert result.detail == "CLI exited 1"


def test_dispatch_kimi_with_config_toml_credentials_ok(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(
        "[providers.kimi]\ntype = \"kimi\"\napi_key = \"sk-test-not-real\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert result.blocker is None
    assert oneshot == []
    assert cli_credentials_ok() is True


def test_probe_code_cli_reports_credentials_missing(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "empty-kimi-home"))
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is False
    assert probe["requires_cli"] is True
    assert probe["requires_kimi_credentials"] is True
    assert probe["blocker"] == NAMED_BLOCKER_CLI_CREDS
    assert NAMED_BLOCKER_CLI_CREDS in probe["error"]


def test_probe_code_cli_credentials_ok_when_config_present(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text("[providers.kimi]\n", encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is True
    assert "blocker" not in probe


def test_claude_cli_does_not_require_kimi_config(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    assert cli_credentials_ok() is True
    raise_if_cli_session_unready()
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["requires_kimi_credentials"] is False
    assert "blocker" not in probe


def test_env_test_template_path_skips_credentials_gate(tmp_path, monkeypatch):
    """ENV=test without REQUIRE_CLI mutation must not refuse generate-start."""
    script = _fake_kimi(tmp_path)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    assert brief_requires_cli() is False
    raise_if_cli_session_unready()
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is False
    assert probe["requires_cli"] is False
    assert "blocker" not in probe


def test_start_runner_build_refuses_when_credentials_missing(tmp_path, monkeypatch):
    from app.factory.build_jobs import start_runner_build
    from app.factory.blueprint import load_blueprint

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    with pytest.raises(CodeCliCredentialsMissing, match=NAMED_BLOCKER_CLI_CREDS):
        start_runner_build(bp, tmp_path / "out")
    assert not (tmp_path / "out" / "build_ledger.jsonl").exists()


def test_approve_and_generate_names_credentials_class_without_takeover(
    tmp_path, monkeypatch
):
    from app.factory import platform_chat_flow
    from app.models.session import ProductDesignState, SessionState

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    root = Path(__file__).resolve().parents[3]
    from app.factory.blueprint import load_blueprint

    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    state = SessionState(
        session_id="sess_cli_creds_missing",
        user_id="test-user",
        product_design=ProductDesignState(
            blueprint=bp.model_dump(mode="json"),
            blueprint_approved=False,
        ),
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is False
    assert result["sse"] == "error"
    assert result["blocker"] == NAMED_BLOCKER_CLI_CREDS
    assert NAMED_BLOCKER_CLI_CREDS in result["summary"]
    assert "never opened" in result["summary"]
    assert "taken over" not in result["summary"].lower()
    assert state.product_design.generation is None
    assert NAMED_BLOCKER_CLI_CREDS in (state.product_design.last_error or "")
