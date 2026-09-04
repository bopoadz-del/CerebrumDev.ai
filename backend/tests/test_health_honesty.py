"""Health must report evaluated capability, not configuration."""

import json

import pytest

import app.main as main


@pytest.mark.asyncio
async def test_health_reports_factory_code_cli_probe(tmp_path, monkeypatch):
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
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KIMI_MOCK", "1")

    # /ready answers with a real status code now (503 when not ready), so it
    # returns a Response rather than a bare dict.
    resp = await main.ready()
    body = json.loads(resp.body)
    assert body["checks"]["llm_configured"] is False, "KIMI_MOCK is not a configured LLM"
    assert body["checks"]["llm_mock"] is True


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
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
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
