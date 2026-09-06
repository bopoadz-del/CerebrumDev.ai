"""DeepSeek V4 Pro as FACTORY_CODE_CLI (Claude Code Anthropic-compat).

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    NAMED_BLOCKER_CLI,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_CREDS,
    NAMED_BLOCKER_CLI_FAILED,
    NAMED_BLOCKER_CLI_MODEL_DENIED,
    NAMED_BLOCKER_CLI_UNUSED,
    brief_dispatch_enabled,
    brief_requires_cli,
    classify_cli_exit,
    cli_credentials_ok,
    cli_requires_deepseek_credentials,
    cli_requires_kimi_credentials,
    deepseek_cli_ready,
    dispatch_compiled_brief,
    ensure_code_cli_credentials,
    inventory_gap_ids,
    probe_code_cli,
    raise_if_cli_session_unready,
    should_factory_llm_generate_gaps,
    thin_stub_success_blocked,
)
from app.factory.build.roles_models import RoleContext
from app.factory.build.workspace import RoleWorkspace
from app.factory.build.authority import BuildRole
from app.factory.code_cli import (
    CLAUDE_PRINT_STDIN_INSTRUCTION,
    DEFAULT_DEEPSEEK_FLASH_MODEL,
    DEFAULT_DEEPSEEK_MODEL,
    DEEPSEEK_ANTHROPIC_BASE_URL,
    DEEPSEEK_API_FLASH_MODEL,
    DEEPSEEK_API_PRO_MODEL,
    DEEPSEEK_CODE_MODEL_ENV,
    REJECTED_DEEPSEEK_BARE_MODEL,
    REJECTED_DEEPSEEK_CLAUDE_MODEL,
    ClaudePrintPromptEmpty,
    claude_print_argv,
    claude_print_log_argv,
    claude_print_prompt,
    code_cli_command,
    deepseek_cli_environ,
    deepseek_code_model,
    deepseek_coder_selected,
    factory_code_provider,
    normalize_deepseek_claude_model,
)


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


def _fake_claude(tmp_path: Path) -> Path:
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _require_cli(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)


def test_deepseek_key_selects_claude_cli(monkeypatch):
    monkeypatch.delenv("FACTORY_CODE_CLI", raising=False)
    monkeypatch.delenv("KIMI_CODE_CLI", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "claude"
    assert deepseek_coder_selected() is True


def test_explicit_kimi_cli_wins_over_deepseek_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    monkeypatch.setenv("FACTORY_CODE_CLI", "kimi")
    assert factory_code_provider() == "kimi"
    assert code_cli_command() == "kimi"
    assert deepseek_coder_selected() is False
    assert cli_requires_kimi_credentials() is True
    assert cli_requires_deepseek_credentials() is False


def test_provider_deepseek_without_key_requires_credentials(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.delenv("FACTORY_CODE_CLI", raising=False)
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "claude"
    assert cli_requires_deepseek_credentials() is True
    assert cli_credentials_ok() is False


def test_deepseek_cli_environ_matches_official_docs(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SUBAGENT_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", raising=False)
    env = deepseek_cli_environ("sk-deepseek-test-not-real")
    assert env["ANTHROPIC_BASE_URL"] == DEEPSEEK_ANTHROPIC_BASE_URL
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-deepseek-test-not-real"
    assert env["ANTHROPIC_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == DEFAULT_DEEPSEEK_FLASH_MODEL
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == DEFAULT_DEEPSEEK_FLASH_MODEL
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "786432"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "ANTHROPIC_API_KEY" not in env
    assert DEFAULT_DEEPSEEK_MODEL.startswith("claude-opus")
    assert DEFAULT_DEEPSEEK_FLASH_MODEL.startswith("claude-haiku")
    assert DEFAULT_DEEPSEEK_MODEL != DEEPSEEK_API_PRO_MODEL
    assert DEFAULT_DEEPSEEK_MODEL != REJECTED_DEEPSEEK_CLAUDE_MODEL
    assert "[" not in DEFAULT_DEEPSEEK_MODEL
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL not in env.values()
    assert DEEPSEEK_API_PRO_MODEL not in env.values()
    assert DEEPSEEK_API_FLASH_MODEL not in env.values()
    assert env["ANTHROPIC_MODEL"] != REJECTED_DEEPSEEK_CLAUDE_MODEL
    assert env["ANTHROPIC_MODEL"] != REJECTED_DEEPSEEK_BARE_MODEL


def test_ensure_deepseek_does_not_write_kimi_file_or_process_anthropic(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["deepseek"]["ok"] is True
    assert result["deepseek"]["model"] == DEFAULT_DEEPSEEK_MODEL
    assert not (tmp_path / "kimi-home" / "config.toml").is_file()
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ
    assert "ANTHROPIC_AUTH_TOKEN" not in __import__("os").environ


def test_ensure_kimi_still_writes_when_deepseek_also_set(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-test-not-real")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["wrote"] is True
    dest = tmp_path / "kimi-home" / "config.toml"
    text = dest.read_text(encoding="utf-8")
    assert "[providers.kimi]" in text
    assert "sk-kimi-test-not-real" in text
    assert result["deepseek"]["ok"] is True


def test_probe_deepseek_credentials_missing(tmp_path, monkeypatch):
    script = _fake_claude(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["provider"] == "deepseek"
    assert probe["requires_deepseek_credentials"] is True
    assert probe["deepseek_key_present"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI_CREDS
    assert "DEEPSEEK_API_KEY" in probe["error"]
    assert "pilot_zip" not in probe["error"]


def test_probe_deepseek_ready(tmp_path, monkeypatch):
    script = _fake_claude(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["provider"] == "deepseek"
    assert probe["requires_deepseek_credentials"] is True
    assert probe["requires_kimi_credentials"] is False
    assert probe["deepseek_key_present"] is True
    assert probe["default_model"] == DEFAULT_DEEPSEEK_MODEL
    assert probe["default_model_configured"] is True
    assert "blocker" not in probe
    raise_if_cli_session_unready()


def test_probe_deepseek_binary_missing(tmp_path, monkeypatch):
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(tmp_path / "no-such-claude"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    probe = probe_code_cli()
    assert probe["available"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI
    assert "DEEPSEEK_API_KEY" in probe["error"]


def test_dispatch_deepseek_uses_print_and_subprocess_env(tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.log"
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        '{ printf "%s\\n" "$0" "$@"; '
        'printf "MODEL=%s\\n" "$ANTHROPIC_MODEL"; '
        'printf "BASE=%s\\n" "$ANTHROPIC_BASE_URL"; '
        'printf "TOKEN_SET=%s\\n" "${ANTHROPIC_AUTH_TOKEN:+yes}"; '
        'printf "STDIN_BYTES=%s\\n" "$(wc -c)"; '
        '} > "$CODE_CLI_ARGV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
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
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert "--print" in logged
    assert "--dangerously-skip-permissions" in logged
    assert "--prompt" not in logged
    assert "@docs/coder_brief.md" not in logged
    assert "TARGET" in logged or "STEP 0" in logged or "INVENTORY" in logged
    assert f"MODEL={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert "--model" in logged
    assert f"MODEL={REJECTED_DEEPSEEK_CLAUDE_MODEL}" not in logged
    assert f"MODEL={REJECTED_DEEPSEEK_BARE_MODEL}" not in logged
    assert "[1m]" not in logged
    assert DEEPSEEK_API_PRO_MODEL not in logged
    assert f"BASE={DEEPSEEK_ANTHROPIC_BASE_URL}" in logged
    assert "TOKEN_SET=yes" in logged
    stdin_line = [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")]
    assert stdin_line, logged
    assert int(stdin_line[0].split("=", 1)[1]) > 0
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is True
    assert "pilot_zip" not in json.dumps(receipt)


def test_default_deepseek_model_is_claude_catalog_opus_id():
    """Live 2.1.x SDK rejects both deepseek-v4-pro[1m] and bare deepseek-v4-pro."""
    assert DEFAULT_DEEPSEEK_MODEL.startswith("claude-opus")
    assert DEFAULT_DEEPSEEK_FLASH_MODEL.startswith("claude-haiku")
    assert DEFAULT_DEEPSEEK_MODEL != REJECTED_DEEPSEEK_CLAUDE_MODEL
    assert DEFAULT_DEEPSEEK_MODEL != REJECTED_DEEPSEEK_BARE_MODEL
    assert DEFAULT_DEEPSEEK_MODEL != DEEPSEEK_API_PRO_MODEL
    assert "[" not in DEFAULT_DEEPSEEK_MODEL
    assert normalize_deepseek_claude_model(REJECTED_DEEPSEEK_CLAUDE_MODEL) == (
        DEFAULT_DEEPSEEK_MODEL
    )
    assert normalize_deepseek_claude_model(REJECTED_DEEPSEEK_BARE_MODEL) == (
        DEFAULT_DEEPSEEK_MODEL
    )
    assert normalize_deepseek_claude_model(DEEPSEEK_API_PRO_MODEL) == (
        DEFAULT_DEEPSEEK_MODEL
    )
    assert normalize_deepseek_claude_model(DEEPSEEK_API_FLASH_MODEL) == (
        DEFAULT_DEEPSEEK_FLASH_MODEL
    )
    assert normalize_deepseek_claude_model("claude-opus-4-8") == "claude-opus-4-8"


def test_deepseek_cli_environ_strips_rejected_1m_suffix(monkeypatch):
    """Leftover Render ANTHROPIC_MODEL=deepseek-v4-pro[1m] remaps to claude-opus."""
    monkeypatch.setenv("ANTHROPIC_MODEL", REJECTED_DEEPSEEK_CLAUDE_MODEL)
    monkeypatch.setenv("DEEPSEEK_CODE_MODEL", REJECTED_DEEPSEEK_CLAUDE_MODEL)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", REJECTED_DEEPSEEK_CLAUDE_MODEL)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", REJECTED_DEEPSEEK_CLAUDE_MODEL)
    assert deepseek_code_model() == DEFAULT_DEEPSEEK_MODEL
    env = deepseek_cli_environ("sk-deepseek-test-not-real")
    assert env["ANTHROPIC_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["ANTHROPIC_MODEL"].startswith("claude-opus")
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL not in env.values()
    assert REJECTED_DEEPSEEK_BARE_MODEL not in env.values()
    assert all("[1m]" not in value for value in env.values())


def test_deepseek_cli_environ_remaps_leftover_bare_deepseek_id(monkeypatch):
    """#370 leftover ANTHROPIC_MODEL=deepseek-v4-pro still remaps to claude-opus."""
    monkeypatch.setenv("ANTHROPIC_MODEL", REJECTED_DEEPSEEK_BARE_MODEL)
    monkeypatch.setenv("DEEPSEEK_CODE_MODEL", REJECTED_DEEPSEEK_BARE_MODEL)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", DEEPSEEK_API_FLASH_MODEL)
    assert deepseek_code_model() == DEFAULT_DEEPSEEK_MODEL
    env = deepseek_cli_environ("sk-deepseek-test-not-real")
    assert env["ANTHROPIC_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["ANTHROPIC_MODEL"].startswith("claude-opus")
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == DEFAULT_DEEPSEEK_FLASH_MODEL
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"].startswith("claude-haiku")
    assert REJECTED_DEEPSEEK_BARE_MODEL not in env.values()
    assert DEEPSEEK_API_FLASH_MODEL not in env.values()


def test_classify_deepseek_429_is_billing():
    blocker, detail = classify_cli_exit(
        1,
        "HTTP 429 from https://api.deepseek.com/anthropic: insufficient quota",
    )
    assert blocker == NAMED_BLOCKER_CLI_BILLING
    assert "FACTORY_CODE_CLI_BILLING" in detail
    assert "≥2h" in detail or "2h" in detail


def test_classify_unrecognized_model_is_model_denied():
    """sess_be217f6d last_event — named MODEL_DENIED, not generic FAILED."""
    live = (
        '[claude-code:unrecognized_model] '
        '{"model":"deepseek-v4-pro[1m]","query_source":"sdk"}'
    )
    blocker, detail = classify_cli_exit(1, live)
    assert blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert blocker != NAMED_BLOCKER_CLI_FAILED
    assert NAMED_BLOCKER_CLI_MODEL_DENIED in detail
    assert NAMED_BLOCKER_CLI_FAILED in detail
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL in detail
    assert DEEPSEEK_CODE_MODEL_ENV in detail
    assert DEFAULT_DEEPSEEK_MODEL in detail
    assert REJECTED_DEEPSEEK_BARE_MODEL in detail
    assert "claude-opus" in detail
    assert "OpenRouter" in detail
    bare_live = (
        '[claude-code:unrecognized_model] '
        '{"model":"deepseek-v4-pro","query_source":"sdk"}'
    )
    bare_blocker, bare_detail = classify_cli_exit(1, bare_live)
    assert bare_blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert REJECTED_DEEPSEEK_BARE_MODEL in bare_detail
    assert DEFAULT_DEEPSEEK_MODEL in bare_detail
    similar, similar_detail = classify_cli_exit(
        1,
        "There's an issue with the selected model (deepseek-v4-pro[1m])",
    )
    assert similar == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL in similar_detail
    billing_wins, _ = classify_cli_exit(
        1,
        live + "\nHTTP 429 from https://api.deepseek.com/anthropic: insufficient quota",
    )
    assert billing_wins == NAMED_BLOCKER_CLI_BILLING
    generic, generic_detail = classify_cli_exit(1, "segfault")
    assert generic == NAMED_BLOCKER_CLI_FAILED
    assert generic_detail == "CLI exited 1"


def test_claude_print_argv_shape():
    brief = (
        "# TARGET\nResidential Lettings — implement the gated Factory C-BRIEF.\n"
        "## STEP 0 INVENTORY\nlettings_core GENERATE\n"
    )
    argv = claude_print_argv("/usr/local/bin/claude", brief)
    assert argv[0] == "/usr/local/bin/claude"
    assert argv[1] == "--print"
    assert argv[2] == brief.strip()
    assert "--dangerously-skip-permissions" in argv
    assert "--add-dir" in argv
    assert "@docs/coder_brief.md" not in argv
    assert "--prompt" not in argv
    logged = claude_print_log_argv("/usr/local/bin/claude", len(brief))
    assert "<docs/coder_brief.md" in logged[2]
    modeled = claude_print_argv(
        "/usr/local/bin/claude", brief, model=REJECTED_DEEPSEEK_BARE_MODEL
    )
    assert modeled[-2:] == ["--model", DEFAULT_DEEPSEEK_MODEL]
    assert REJECTED_DEEPSEEK_BARE_MODEL not in modeled
    assert "--prompt" not in modeled


def test_claude_print_argv_rejects_empty_and_bare_at_path():
    for empty in ("", "   ", "@docs/coder_brief.md", "@docs/coder_brief.md\n"):
        with pytest.raises(ClaudePrintPromptEmpty):
            claude_print_argv("/usr/local/bin/claude", empty)
        with pytest.raises(ClaudePrintPromptEmpty):
            claude_print_prompt(empty)


def test_claude_print_argv_large_brief_keeps_nonempty_print_arg():
    brief = "# TARGET\n" + ("x" * 90_000)
    argv = claude_print_argv("/usr/local/bin/claude", brief)
    assert argv[1] == "--print"
    assert argv[2] == CLAUDE_PRINT_STDIN_INSTRUCTION
    assert argv[2].strip()
    assert "@docs/coder_brief.md" not in argv


def test_classify_claude_print_missing_input():
    blocker, detail = classify_cli_exit(
        1,
        "Error: Input must be provided either through stdin or as a "
        "prompt argument when using --print",
    )
    assert blocker == "FACTORY_CODE_CLI_FAILED"
    assert "coder_brief.md" in detail
    assert "@docs/coder_brief.md" in detail


_STRICT_CLAUDE = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import os
    import sys
    from pathlib import Path

    argv = sys.argv[1:]
    prompt = ""
    saw_print = False
    i = 0
    while i < len(argv):
        if argv[i] in ("--print", "-p"):
            saw_print = True
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                prompt = argv[i + 1]
                i += 2
                continue
        i += 1
    stdin_data = sys.stdin.read() if not sys.stdin.isatty() else ""

    def fail() -> None:
        print(
            "Error: Input must be provided either through stdin or as a "
            "prompt argument when using --print"
        )
        raise SystemExit(1)

    if not saw_print:
        fail()
    if prompt.startswith("@") and "/" in prompt and "\n" not in prompt:
        if not stdin_data.strip():
            fail()
    if not prompt.strip() and not stdin_data.strip():
        fail()
    body = f"{prompt}\n{stdin_data}"
    if not any(tok in body for tok in ("TARGET", "STEP 0", "INVENTORY")):
        fail()
    dest = os.environ.get("CODE_CLI_ARGV_LOG")
    if dest:
        Path(dest).write_text(
            "ARGC={0}\nPRINT={1}\nPROMPT_BYTES={2}\nSTDIN_BYTES={3}\n"
            "MODEL={4}\nBASE={5}\nTOKEN_SET={6}\nBODY_HEAD={7}\n".format(
                len(sys.argv),
                "yes" if saw_print else "no",
                len(prompt),
                len(stdin_data),
                os.environ.get("ANTHROPIC_MODEL", ""),
                os.environ.get("ANTHROPIC_BASE_URL", ""),
                "yes" if os.environ.get("ANTHROPIC_AUTH_TOKEN") else "no",
                body[:240].replace("\n", " "),
            ),
            encoding="utf-8",
        )
    Path("docs").mkdir(parents=True, exist_ok=True)
    Path("docs/claude_received_brief.txt").write_text(
        f"ok prompt={len(prompt)} stdin={len(stdin_data)}\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
    """
).lstrip()


def _strict_claude(tmp_path: Path) -> Path:
    script = tmp_path / "claude"
    script.write_text(_STRICT_CLAUDE, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_strict_claude_mock_fails_on_empty_print_and_at_file(tmp_path):
    """Live Claude Code 2.1.x contract: no prompt/stdin → Input must be provided."""
    import subprocess

    script = _strict_claude(tmp_path)
    old_shape = subprocess.run(
        [
            str(script),
            "--print",
            "--dangerously-skip-permissions",
            "--add-dir",
            ".",
            "@docs/coder_brief.md",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        input="",
    )
    assert old_shape.returncode == 1
    assert "Input must be provided" in (old_shape.stdout + old_shape.stderr)

    empty_print = subprocess.run(
        [str(script), "--print", "--dangerously-skip-permissions"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        input="",
    )
    assert empty_print.returncode == 1
    assert "Input must be provided" in (empty_print.stdout + empty_print.stderr)


def test_dispatch_deepseek_strict_claude_receives_brief(tmp_path, monkeypatch):
    """FACTORY_CODE_CLI=claude must feed coder_brief.md as --print + stdin."""
    argv_log = tmp_path / "argv.log"
    script = _strict_claude(tmp_path)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
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
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert "PRINT=yes" in logged
    assert "TOKEN_SET=yes" in logged
    assert f"MODEL={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert f"MODEL={REJECTED_DEEPSEEK_CLAUDE_MODEL}" not in logged
    assert f"MODEL={REJECTED_DEEPSEEK_BARE_MODEL}" not in logged
    assert f"BASE={DEEPSEEK_ANTHROPIC_BASE_URL}" in logged
    prompt_bytes = int(
        [ln for ln in logged.splitlines() if ln.startswith("PROMPT_BYTES=")][0].split(
            "=", 1
        )[1]
    )
    stdin_bytes = int(
        [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")][0].split(
            "=", 1
        )[1]
    )
    assert prompt_bytes > 0
    assert stdin_bytes > 0
    received = tmp_path / "build" / "docs" / "claude_received_brief.txt"
    assert received.is_file(), "mock claude must write a workspace artifact"
    assert "ok" in received.read_text(encoding="utf-8")
    session_log = (tmp_path / "build" / "docs" / "coder_session.log").read_text(
        encoding="utf-8"
    )
    assert "--print" in session_log
    assert "@docs/coder_brief.md" not in session_log.split("$", 1)[-1].split("\n", 1)[0]
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def _arm_deepseek_cli(tmp_path, monkeypatch, script: Path | None = None) -> Path:
    cli = script or _fake_claude(tmp_path)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(cli))
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test-not-real")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    return cli


def test_deepseek_ready_requires_cli_even_when_env_test(tmp_path, monkeypatch):
    """CLI ready + DeepSeek key is not optional just because ENV=test."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    assert deepseek_cli_ready() is True
    assert brief_requires_cli() is True
    raise_if_cli_session_unready()


def test_deepseek_ready_requires_cli_even_when_require_cli_off(tmp_path, monkeypatch):
    """Leftover FACTORY_BRIEF_REQUIRE_CLI=0 must not skip Claude→DeepSeek."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "0")
    assert deepseek_cli_ready() is True
    assert brief_requires_cli() is True
    raise_if_cli_session_unready()


def test_deepseek_ready_forces_brief_dispatch(tmp_path, monkeypatch):
    """Leftover FACTORY_BRIEF_DISPATCH=0 must not restore per-cap OpenRouter."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    assert brief_dispatch_enabled() is True
    assert brief_requires_cli() is True


def test_dispatch_deepseek_ready_never_calls_factory_llm_even_with_generate_gap(
    tmp_path, monkeypatch
):
    """C-BRIEF lock: CLI ready + DeepSeek key ⇒ via=cli, not OpenRouter."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )

    class _Gap:
        capability_id = "lettings_core"
        block_ids = ()
        strategy = "GENERATE"
        notes = "gap"

    class _Plan:
        capabilities = (_Gap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _Plan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    assert any(item.is_gap for item in compiled.inventory)
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert result.factory_llm_generate_fallthrough is False
    assert oneshot == []
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["via"] == "cli"
    assert receipt["factory_llm_generate_fallthrough"] is False
    assert "pilot_zip" not in json.dumps(receipt)


def test_dispatch_deepseek_billing_does_not_openrouter_fallthrough(
    tmp_path, monkeypatch
):
    """DeepSeek CLI ready + 429 must not hand GENERATE gaps to OpenRouter."""
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'HTTP 429 from https://api.deepseek.com/anthropic: insufficient quota'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )

    class _Gap:
        capability_id = "lettings_core"
        block_ids = ()
        strategy = "GENERATE"
        notes = "gap"

    class _Plan:
        capabilities = (_Gap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _Plan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_BILLING
    assert deepseek_cli_ready() is True
    assert should_factory_llm_generate_gaps(compiled, result) is False
    assert result.factory_llm_generate_fallthrough is False
    assert oneshot == [], "DeepSeek-ready C-BRIEF must not call OpenRouter factory coder"
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_dispatch_unrecognized_model_is_denied_no_openrouter(
    tmp_path, monkeypatch
):
    """Claude Code unrecognized_model must fail-closed — no OpenRouter."""
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        'echo \'[claude-code:unrecognized_model] '
        '{"model":"deepseek-v4-pro[1m]","query_source":"sdk"}\'\n'
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )

    class _Gap:
        capability_id = "lettings_core"
        block_ids = ()
        strategy = "GENERATE"
        notes = "gap"

    class _Plan:
        capabilities = (_Gap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _Plan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL in (result.detail or "")
    assert DEEPSEEK_CODE_MODEL_ENV in (result.detail or "")
    assert deepseek_cli_ready() is True
    assert should_factory_llm_generate_gaps(compiled, result) is False
    assert result.factory_llm_generate_fallthrough is False
    assert oneshot == [], "unrecognized_model must not fall through to OpenRouter"
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert "pilot_zip" not in json.dumps(receipt)


def test_leftover_47s_wall_remaps_when_deepseek_ready(tmp_path, monkeypatch):
    """Leftover ~47s wall cannot host Claude→DeepSeek; remap to stage 1."""
    from app.factory.build_jobs import _wall_clock_s

    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORY_BUILD_WALL_CLOCK_S", "47")
    assert deepseek_cli_ready() is True
    assert _wall_clock_s() == 1800.0


def test_explicit_2h_wall_still_honoured_with_deepseek(tmp_path, monkeypatch):
    from app.factory.build_jobs import _wall_clock_s

    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORY_BUILD_WALL_CLOCK_S", "7200")
    assert _wall_clock_s() == 7200.0


def test_collector_skips_factory_llm_when_deepseek_ready(tmp_path, monkeypatch):
    """COLLECTOR must not burn leftover wall on OpenRouter before C-BRIEF."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import run_collector
    from app.factory.build.roles_models import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    _arm_deepseek_cli(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: called.append(kw)
        or {"reviews": [], "model": "minimax/minimax-m3:free"},
    )
    notes = []

    class _Cap:
        capability_id = "lettings_core"
        block_ids = ("persist",)
        notes = "core"

    class _Plan:
        capabilities = (_Cap(),)

    ctx = RoleContext(
        role=BuildRole.COLLECTOR,
        workspace=RoleWorkspace(BuildRole.COLLECTOR, tmp_path / "build"),
        blueprint=_Blueprint(),
        plan=_Plan(),
        state={},
        progress=lambda detail, payload: notes.append(detail),
    )
    result = run_collector(ctx)
    assert result.ok
    assert called == [], "DeepSeek-ready COLLECTOR must not call factory LLM"
    assert any("FACTORY_CODE_CLI" in n and "DeepSeek" in n for n in notes)


LETTINGS_STORE = {
    "analytics",
    "team",
    "workflow",
    "notification",
    "document_engine",
}


class _ReuseCap:
    def __init__(self, cid, bids, strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(bids)
        self.strategy = strategy
        self.notes = cid


class _LettingsReusePlan:
    capabilities = (
        _ReuseCap("unit_registry_and_vacancy_tracking", ["analytics"], "REUSE"),
        _ReuseCap(
            "viewing_management", ["team", "workflow", "notification"], "COMPOSE"
        ),
        _ReuseCap("maintenance_issue_tracking", ["team"], "REUSE"),
        _ReuseCap(
            "tenancy_application_pipeline", ["team", "document_engine"], "COMPOSE"
        ),
    )


def test_dispatch_leftover_1m_env_sends_catalog_id(tmp_path, monkeypatch):
    """Render leftover ANTHROPIC_MODEL=[1m] remaps to claude-opus + stdin."""
    argv_log = tmp_path / "argv.log"
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        '{ printf "%s\\n" "$0" "$@"; '
        'printf "MODEL=%s\\n" "$ANTHROPIC_MODEL"; '
        'printf "OPUS=%s\\n" "$ANTHROPIC_DEFAULT_OPUS_MODEL"; '
        'printf "STDIN_BYTES=%s\\n" "$(wc -c)"; '
        '} > "$CODE_CLI_ARGV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("ANTHROPIC_MODEL", REJECTED_DEEPSEEK_CLAUDE_MODEL)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", REJECTED_DEEPSEEK_CLAUDE_MODEL)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
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
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert f"MODEL={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert f"OPUS={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert "--model" in logged
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL not in logged
    assert REJECTED_DEEPSEEK_BARE_MODEL not in logged
    assert "--print" in logged
    assert "@docs/coder_brief.md" not in logged
    stdin_line = [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")]
    assert stdin_line and int(stdin_line[0].split("=", 1)[1]) > 0
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_dispatch_leftover_bare_deepseek_id_sends_claude_opus(tmp_path, monkeypatch):
    """#370 leftover ANTHROPIC_MODEL=deepseek-v4-pro remaps to claude-opus."""
    argv_log = tmp_path / "argv.log"
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        '{ printf "%s\\n" "$0" "$@"; '
        'printf "MODEL=%s\\n" "$ANTHROPIC_MODEL"; '
        'printf "BASE=%s\\n" "$ANTHROPIC_BASE_URL"; '
        'printf "TOKEN_SET=%s\\n" "${ANTHROPIC_AUTH_TOKEN:+yes}"; '
        'printf "STDIN_BYTES=%s\\n" "$(wc -c)"; '
        '} > "$CODE_CLI_ARGV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("ANTHROPIC_MODEL", REJECTED_DEEPSEEK_BARE_MODEL)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
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
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert f"MODEL={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert DEFAULT_DEEPSEEK_MODEL.startswith("claude-opus")
    assert "--model" in logged
    assert REJECTED_DEEPSEEK_BARE_MODEL not in logged
    assert f"BASE={DEEPSEEK_ANTHROPIC_BASE_URL}" in logged
    assert "TOKEN_SET=yes" in logged
    stdin_line = [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")]
    assert stdin_line and int(stdin_line[0].split("=", 1)[1]) > 0
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_dispatch_deepseek_ready_all_reuse_compose_uses_cli(tmp_path, monkeypatch):
    """Store-complete REUSE/COMPOSE (no GENERATE gaps) still dispatches via=cli."""
    argv_log = tmp_path / "argv.log"
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        '{ printf "%s\\n" "$0" "$@"; '
        'printf "STDIN_BYTES=%s\\n" "$(wc -c)"; '
        '} > "$CODE_CLI_ARGV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    ctx = _ctx(tmp_path)
    ctx.plan = _LettingsReusePlan()
    compiled = compile_brief(
        ctx.blueprint, ctx.plan, store_ids=LETTINGS_STORE
    )
    assert inventory_gap_ids(compiled) == []
    assert all(not item.is_gap for item in compiled.inventory)
    assert "Do not skip the CLI because inventory_gaps is empty" in compiled.text
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert oneshot == []
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ
    logged = argv_log.read_text(encoding="utf-8")
    assert "--print" in logged
    assert "--prompt" not in logged
    assert "@docs/coder_brief.md" not in logged
    assert "TARGET" in logged or "STEP 0" in logged or "INVENTORY" in logged
    stdin_line = [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")]
    assert stdin_line, logged
    assert int(stdin_line[0].split("=", 1)[1]) > 0
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["via"] == "cli"
    assert "pilot_zip" not in json.dumps(receipt)


def test_writer_all_reuse_compose_dispatches_cli_when_deepseek_ready(
    tmp_path, monkeypatch
):
    """WRITER path: DeepSeek ready + all-REUSE/COMPOSE ⇒ via=cli, not skip."""
    from app.factory.build.roles import run_writer

    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    plan = _LettingsReusePlan()
    compiled = compile_brief(_Blueprint(), plan, store_ids=LETTINGS_STORE)
    assert inventory_gap_ids(compiled) == []
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_Blueprint(),
            plan=plan,
            state={
                "resolved_blocks": tuple(LETTINGS_STORE),
                "vendored_blocks": tuple(LETTINGS_STORE),
            },
        )
    )
    assert result.ok, result.detail
    assert oneshot == []
    receipt = json.loads(
        (dest / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["via"] == "cli"
    assert receipt.get("factory_llm_generate_fallthrough") is False
    assert "pilot_zip" not in json.dumps(receipt)
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_budget_inspect_does_not_success_thin_stubs_before_cli_when_deepseek_ready(
    tmp_path, monkeypatch
):
    """8s stub_rate=1.0 written=0 must not SUCCESS when DeepSeek CLI is ready."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.budget_inspect import inspect_build, inspect_decision
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.runner import Outcome, RoleRunner
    from app.factory.blueprint import load_blueprint

    _arm_deepseek_cli(tmp_path, monkeypatch)
    assert deepseek_cli_ready() is True
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc")
    for cap in (
        "unit_registry_and_vacancy_tracking",
        "viewing_management",
        "maintenance_issue_tracking",
        "tenancy_application_pipeline",
    ):
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} (deterministic contract template)",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": "deterministic contract template",
            },
        )
    snap = inspect_build(ledger)
    assert snap["agent_written"] == 0
    assert snap["templated"] == 4
    assert snap["stub_rate"] == 1.0
    assert snap["cli_attempted"] is False
    decided = inspect_decision(
        elapsed_s=8.0,
        current_wall_s=1800.0,
        snapshot=snap,
        stage="pilot_open",
    )
    assert decided["decision"] == "await_cli"
    assert decided["decision"] != "hard_stop"
    blocker = thin_stub_success_blocked(
        snapshot=snap, elapsed_s=8.0, ledger=ledger
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_UNUSED in blocker

    root = Path(__file__).resolve().parents[3]
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        out,
        ledger=ledger,
    )
    runner._run_started = runner.clock()
    runner.state["brief_dispatch"] = {"via": "skipped"}
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is False
    assert outcome.outcome.value == "FAILED_ROLE_ERROR"
    assert NAMED_BLOCKER_CLI_UNUSED in (outcome.detail or "")
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert "pilot_zip" not in (outcome.detail or "")
