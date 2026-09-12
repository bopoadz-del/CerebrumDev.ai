"""Health must report evaluated capability, not configuration."""

import json

import pytest

import app.main as main


def _clear_cursor_keys(monkeypatch) -> None:
    from app.factory.build.cli_pivot import CURSOR_KEY_ENVS

    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_health_reports_factory_code_cli_probe(tmp_path, monkeypatch):
    _clear_cursor_keys(monkeypatch)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_CODE_CLI", str(tmp_path / "no-such-coder"))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)

    body = await main.health()
    probe = body["factory_code_cli"]
    assert probe["available"] is False
    assert probe["blocker"] == "FACTORY_CODE_CLI_UNAVAILABLE"
    assert probe["requires_cli"] is True


@pytest.mark.asyncio
async def test_health_reports_credentials_missing_when_kimi_present(tmp_path, monkeypatch):
    _clear_cursor_keys(monkeypatch)
    fake = _write_fake_cli(tmp_path)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_CODE_CLI", str(fake))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))

    body = await main.health()
    probe = body["factory_code_cli"]
    assert probe["available"] is True
    assert probe["credentials_file_present"] is False
    assert probe["blocker"] == "FACTORY_CODE_CLI_CREDENTIALS_MISSING"
    assert probe["requires_cli"] is True


@pytest.mark.asyncio
async def test_health_cursor_ba_does_not_report_kimi_creds_missing(tmp_path, monkeypatch):
    fake = _write_fake_cli(tmp_path)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_CODE_CLI", str(fake))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-key")

    body = await main.health()
    probe = body["factory_code_cli"]
    assert probe["available"] is True
    assert probe["credentials_file_present"] is False
    assert probe["cursor_ba_available"] is True
    assert probe["requires_cli"] is False
    assert probe["requires_kimi_credentials"] is False
    assert "blocker" not in probe


@pytest.mark.asyncio
async def test_health_reports_deepseek_credentials_missing(tmp_path, monkeypatch):
    _clear_cursor_keys(monkeypatch)
    fake = tmp_path / "kimi"
    fake.write_text("#!/bin/sh\necho kimi 0.41.0\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_CODE_CLI", str(fake))
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    body = await main.health()
    probe = body["factory_code_cli"]
    assert probe["available"] is True
    assert probe["provider"] == "deepseek"
    assert probe["deepseek_key_present"] is False
    assert probe["blocker"] == "FACTORY_CODE_CLI_CREDENTIALS_MISSING"
    assert "DEEPSEEK_API_KEY" in probe["error"]


@pytest.mark.asyncio
async def test_health_reports_deepseek_ready(tmp_path, monkeypatch):
    fake = tmp_path / "kimi"
    fake.write_text("#!/bin/sh\necho kimi 0.41.0\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_CODE_CLI", str(fake))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)

    body = await main.health()
    probe = body["factory_code_cli"]
    assert probe["available"] is True
    assert probe["provider"] == "deepseek"
    assert probe["deepseek_key_present"] is True
    assert probe["default_model"] == "deepseek-v4-pro"
    assert not probe["default_model"].startswith("claude-opus")
    assert "[1m]" not in probe["default_model"]
    assert "blocker" not in probe


@pytest.mark.asyncio
async def test_health_reports_no_model_when_config_lacks_default_model(
    tmp_path, monkeypatch
):
    _clear_cursor_keys(monkeypatch)
    fake = _write_fake_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(
        "[providers.kimi]\ntype = \"kimi\"\napi_key = \"sk-test-not-real\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_CODE_CLI", str(fake))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    body = await main.health()
    probe = body["factory_code_cli"]
    assert probe["available"] is True
    assert probe["credentials_file_present"] is True
    assert probe["default_model_configured"] is False
    assert probe["blocker"] == "FACTORY_CODE_CLI_NO_MODEL"
    assert probe["requires_cli"] is True


@pytest.mark.asyncio
async def test_health_kimi_flag_without_binary_is_not_capability(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("KIMI_WORKBENCH_ENABLED", "true")
    monkeypatch.setenv("KIMI_CODE_CLI", str(tmp_path / "no-such-kimi"))

    body = await main.health()
    assert body["kimi_workbench_enabled"] is False, (
        "flag=true with no binary must not be reported as capability"
    )
    probe = body["kimi_workbench"]
    assert probe["flag_enabled"] is True
    assert probe["cli_ok"] is False


def _write_fake_cli(tmp_path):
    """A stand-in coding CLI that answers `--version` successfully.

    Per-platform rather than a `#!/bin/sh` script: Windows cannot exec a
    shebang file and raised WinError 193, so this test only ever ran on Linux
    — on the machine the factory is developed on it was permanently red. A
    `.cmd` launched by absolute path runs without shell=True.
    """
    import os

    if os.name == "nt":
        cli = tmp_path / "kimi.cmd"
        cli.write_text("@echo off\r\necho kimi 9.9.9\r\nexit /b 0\r\n", encoding="utf-8")
        return cli
    cli = tmp_path / "kimi"
    cli.write_text("#!/bin/sh\necho kimi 9.9.9\nexit 0\n", encoding="utf-8")
    cli.chmod(0o755)
    return cli


@pytest.mark.asyncio
async def test_health_kimi_capability_true_when_cli_responds(tmp_path, monkeypatch):
    fake = _write_fake_cli(tmp_path)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("KIMI_WORKBENCH_ENABLED", "true")
    monkeypatch.setenv("KIMI_CODE_CLI", str(fake))

    body = await main.health()
    assert body["kimi_workbench_enabled"] is True
    assert body["kimi_workbench"]["cli_ok"] is True


@pytest.mark.asyncio
async def test_ready_does_not_count_kimi_mock_as_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "LLM_PROVIDER",
        "OPENROUTER_API_KEY",
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    _clear_cursor_keys(monkeypatch)
    monkeypatch.setenv("KIMI_MOCK", "1")

    # /ready answers with a real status code now (503 when not ready), so it
    # returns a Response rather than a bare dict.
    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_configured"] is False, "KIMI_MOCK is not a configured LLM"
    assert body["checks"]["llm_mock"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("  yes  ", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("garbage", False),
        (None, False),
    ],
)
async def test_ready_llm_mock_uses_llm_config_truthy(tmp_path, monkeypatch, raw, expected):
    """KIMI_MOCK must use llm_config._truthy (1/true/yes/on), not bool(env).

    ``bool(os.getenv("KIMI_MOCK"))`` treated any non-empty string —
    including ``"0"`` and ``"false"`` — as mock. /ready must match the
    same helper that get_llm_config / get_factory_llm_config already use.
    """
    from app.core.llm_config import _truthy

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    if raw is None:
        monkeypatch.delenv("KIMI_MOCK", raising=False)
    else:
        monkeypatch.setenv("KIMI_MOCK", raw)

    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_mock"] is expected
    assert body["checks"]["llm_mock"] is _truthy("KIMI_MOCK")


@pytest.mark.asyncio
async def test_ready_does_not_count_provider_without_a_key_as_llm(tmp_path, monkeypatch):
    """LLM_PROVIDER is pinned in render.yaml; it is not a credential."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    _clear_cursor_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "cursor")
    monkeypatch.delenv("KIMI_MOCK", raising=False)

    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_configured"] is False, (
        "LLM_PROVIDER without a key must not report llm_configured"
    )


@pytest.mark.asyncio
async def test_ready_llm_configured_when_a_key_is_present(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", "sk-not-a-real-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_configured"] is True


@pytest.mark.asyncio
async def test_ready_counts_cursor_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("LLM_PROVIDER", "cursor")
    monkeypatch.setenv("CURSOR_API_KEY", "crsr-test-not-real")
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_configured"] is True


@pytest.mark.asyncio
async def test_ready_counts_openrouter_key_when_chat_host_is_openrouter(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_configured"] is True


_LLM_PRESENCE_BOOLS = (
    "cerebrum_chat_llm_api_key_present",
    "cerebrum_llm_api_key_present",
    "kimi_api_key_present",
    "openrouter_api_key_present",
    "cursor_api_key_present",
    "chat_http_api_key_present",
)


def _clear_llm_keys(monkeypatch) -> None:
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
        "LLM_PROVIDER",
        "CEREBRUM_CHAT_LLM_BASE_URL",
        "CEREBRUM_LLM_BASE_URL",
        "KIMI_MOCK",
    ):
        monkeypatch.delenv(var, raising=False)
    _clear_cursor_keys(monkeypatch)


@pytest.mark.asyncio
async def test_ready_llm_details_all_absent_when_no_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    _clear_llm_keys(monkeypatch)

    resp = await main.ready()
    llm = json.loads(resp.body)["details"]["llm"]
    for name in _LLM_PRESENCE_BOOLS:
        assert llm[name] is False, name
    assert llm["llm_provider"] == ""
    assert llm["chat_http_base_url_host"] == ""
    assert llm["chat_http_error"] == ""


@pytest.mark.asyncio
async def test_ready_llm_details_see_cerebrum_chat_key(tmp_path, monkeypatch):
    """Process-visible CEREBRUM_CHAT_LLM_API_KEY must show as present, not leaked."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    _clear_llm_keys(monkeypatch)
    secret = "sk-or-chat-ready-must-not-leak"
    monkeypatch.setenv("LLM_PROVIDER", "cursor")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", secret)
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_BASE_URL", "https://openrouter.ai/api/v1")

    resp = await main.ready()
    body = json.loads(resp.body)
    llm = body["details"]["llm"]
    assert llm["cerebrum_chat_llm_api_key_present"] is True
    assert llm["chat_http_api_key_present"] is True
    assert llm["cursor_api_key_present"] is False
    assert llm["llm_provider"] == "cursor"
    assert llm["chat_http_base_url_host"] == "openrouter.ai"
    assert llm["chat_http_error"] == ""
    dumped = json.dumps(body)
    assert secret not in dumped
    assert "openrouter.ai/api/v1" not in dumped


@pytest.mark.asyncio
async def test_ready_llm_details_prove_chat_key_missing_under_cursor(
    tmp_path, monkeypatch
):
    """Live-bug shape: dashboard may show the chat key; this process does not."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    _clear_llm_keys(monkeypatch)
    ba = "crsr-ba-ready-must-not-leak"
    monkeypatch.setenv("LLM_PROVIDER", "cursor")
    monkeypatch.setenv("CURSOR_API_KEY", ba)

    resp = await main.ready()
    body = json.loads(resp.body)
    llm = body["details"]["llm"]
    assert llm["cerebrum_chat_llm_api_key_present"] is False
    assert llm["cursor_api_key_present"] is True
    assert llm["chat_http_api_key_present"] is False
    assert llm["llm_provider"] == "cursor"
    assert "CEREBRUM_CHAT_LLM_API_KEY" in llm["chat_http_error"]
    dumped = json.dumps(body)
    assert ba not in dumped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_name,flag",
    [
        ("CEREBRUM_CHAT_LLM_API_KEY", "cerebrum_chat_llm_api_key_present"),
        ("CEREBRUM_LLM_API_KEY", "cerebrum_llm_api_key_present"),
        ("KIMI_API_KEY", "kimi_api_key_present"),
        ("OPENROUTER_API_KEY", "openrouter_api_key_present"),
        ("CURSOR_API_KEY", "cursor_api_key_present"),
    ],
)
async def test_ready_llm_details_presence_flags_are_booleans(
    tmp_path, monkeypatch, env_name, flag
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    _clear_llm_keys(monkeypatch)
    secret = f"sk-ready-{env_name}-not-real"
    monkeypatch.setenv(env_name, secret)

    resp = await main.ready()
    body = json.loads(resp.body)
    llm = body["details"]["llm"]
    for name in _LLM_PRESENCE_BOOLS:
        assert isinstance(llm[name], bool), name
    assert llm[flag] is True
    assert secret not in json.dumps(body)

    monkeypatch.setenv(env_name, "   ")
    resp = await main.ready()
    llm = json.loads(resp.body)["details"]["llm"]
    assert llm[flag] is False


@pytest.mark.asyncio
async def test_ready_llm_details_provider_is_raw_not_normalised(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    _clear_llm_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "moonshot")
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-ready-not-real")

    resp = await main.ready()
    llm = json.loads(resp.body)["details"]["llm"]
    assert llm["llm_provider"] == "moonshot"
    assert llm["kimi_api_key_present"] is True
    assert llm["chat_http_api_key_present"] is True


@pytest.mark.asyncio
async def test_ready_llm_details_host_strips_userinfo(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    _clear_llm_keys(monkeypatch)
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-chat-ready-not-real")
    monkeypatch.setenv(
        "CEREBRUM_CHAT_LLM_BASE_URL", "https://user:s3cret-pass@chat.example.test/v1"
    )

    resp = await main.ready()
    body = json.loads(resp.body)
    llm = body["details"]["llm"]
    assert llm["chat_http_base_url_host"] == "chat.example.test"
    dumped = json.dumps(body)
    assert "s3cret-pass" not in dumped
    assert "user:s3cret-pass" not in dumped
