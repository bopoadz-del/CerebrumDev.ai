"""FACTORY_CODE_CLI selection + DeepSeek / Kimi / Claude credential helpers.

Floor C-BRIEF dispatches one compiled brief through this CLI. Chat / architect
stay on their own LLM config (OpenRouter free in production) and must not
consume ``DEEPSEEK_API_KEY``.

DeepSeek V4 Pro is reached through the official Claude Code Anthropic-compat
endpoint (https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/).
Those ``ANTHROPIC_*`` names are injected into the Claude Code **subprocess**
only — never process-wide — so ``LLM_PROVIDER=claude`` cannot silently bill
DeepSeek for Floor chat.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

#: Provider-agnostic name for the agentic coding CLI. ``KIMI_CODE_CLI`` stays
#: honoured so existing deployments keep working unchanged; point
#: FACTORY_CODE_CLI at the Claude Code CLI to use Claude / DeepSeek.
CODE_CLI_ENV = "FACTORY_CODE_CLI"
LEGACY_CODE_CLI_ENV = "KIMI_CODE_CLI"
CODE_PROVIDER_ENV = "FACTORY_CODE_PROVIDER"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_CODE_MODEL_ENV = "DEEPSEEK_CODE_MODEL"

DEFAULT_KIMI_CLI = "kimi"
DEFAULT_DEEPSEEK_CLI = "claude"

#: Official Claude Code model ids on the DeepSeek Anthropic-compat endpoint.
#: OpenAI-format API uses ``deepseek-v4-pro`` (no suffix); prefer ``[1m]``
#: here. Override with ``ANTHROPIC_MODEL`` / ``DEEPSEEK_CODE_MODEL``.
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro[1m]"
DEFAULT_DEEPSEEK_FLASH_MODEL = "deepseek-v4-flash"
DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_AUTO_COMPACT_WINDOW = "786432"


def _cli_basename(command: str) -> str:
    name = (command or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    return name.lower()


def is_claude_code_cli(command: Optional[str] = None) -> bool:
    return "claude" in _cli_basename(command or "")


def is_kimi_code_cli(command: Optional[str] = None) -> bool:
    return "kimi" in _cli_basename(command or "")


def deepseek_api_key() -> str:
    return os.getenv(DEEPSEEK_API_KEY_ENV, "").strip()


def factory_code_provider() -> str:
    """Who authenticates FACTORY_CODE_CLI. Not the Floor chat LLM.

    Explicit ``FACTORY_CODE_PROVIDER`` wins. Otherwise:
    * ``FACTORY_CODE_CLI`` / ``KIMI_CODE_CLI`` whose basename contains ``kimi``
      stays on the Kimi path even when ``DEEPSEEK_API_KEY`` is also set.
    * ``DEEPSEEK_API_KEY`` (or provider=deepseek) selects DeepSeek → ``claude``.
    * a ``claude`` binary without DeepSeek signals is Anthropic-native Claude.
    * otherwise Kimi (historical default).
    """
    cli = (
        os.getenv(CODE_CLI_ENV, "").strip()
        or os.getenv(LEGACY_CODE_CLI_ENV, "").strip()
    )
    # An explicit Kimi binary keeps the Kimi path even if a DeepSeek key is set.
    if cli and is_kimi_code_cli(cli):
        return "kimi"
    explicit = os.getenv(CODE_PROVIDER_ENV, "").strip().lower()
    if explicit == "moonshot":
        return "kimi"
    if explicit in {"deepseek", "kimi", "claude"}:
        return explicit
    if deepseek_api_key():
        return "deepseek"
    if cli and is_claude_code_cli(cli):
        return "claude"
    return "kimi"


def deepseek_coder_selected(command: Optional[str] = None) -> bool:
    """True when C-BRIEF should authenticate Claude Code via DeepSeek."""
    cli = (command or "").strip()
    if cli and is_kimi_code_cli(cli):
        return False
    if factory_code_provider() == "deepseek":
        return True
    if cli and is_claude_code_cli(cli) and (
        deepseek_api_key()
        or os.getenv(CODE_PROVIDER_ENV, "").strip().lower() == "deepseek"
    ):
        return True
    return False


def code_cli_command(default: Optional[str] = None) -> str:
    """The agentic coder CLI to invoke. FACTORY_CODE_CLI wins, then legacy.

    When neither CLI name is set, DeepSeek V4 Pro (``claude``) is the default
    Factory coding path if ``DEEPSEEK_API_KEY`` is present or
    ``FACTORY_CODE_PROVIDER=deepseek``. Otherwise ``kimi``.
    """
    explicit = os.getenv(CODE_CLI_ENV, "").strip() or os.getenv(
        LEGACY_CODE_CLI_ENV, ""
    ).strip()
    if explicit:
        return explicit
    if default is not None:
        return default
    if factory_code_provider() == "deepseek":
        return DEFAULT_DEEPSEEK_CLI
    return DEFAULT_KIMI_CLI


def deepseek_code_model() -> str:
    """Primary coding model for the Claude Code → DeepSeek subprocess."""
    for name in (
        "ANTHROPIC_MODEL",
        DEEPSEEK_CODE_MODEL_ENV,
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
    ):
        raw = os.getenv(name, "").strip()
        if raw:
            return raw
    return DEFAULT_DEEPSEEK_MODEL


def deepseek_flash_model() -> str:
    for name in ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        raw = os.getenv(name, "").strip()
        if raw:
            return raw
    return DEFAULT_DEEPSEEK_FLASH_MODEL


def deepseek_model_configured() -> bool:
    """True when a DeepSeek coding model id is resolvable (compiled default)."""
    return bool(deepseek_code_model())


def deepseek_cli_environ(key: Optional[str] = None) -> Dict[str, str]:
    """Official DeepSeek ↔ Claude Code env for the CLI subprocess only.

    Does not set ``ANTHROPIC_API_KEY`` (that arms Floor/architect Claude).
    Operator overrides on the named vars are honoured except
    ``ANTHROPIC_AUTH_TOKEN``, which always comes from ``DEEPSEEK_API_KEY``.
    """
    token = (key if key is not None else deepseek_api_key()).strip()
    primary = deepseek_code_model()
    flash = deepseek_flash_model()
    base = os.getenv("ANTHROPIC_BASE_URL", "").strip() or DEEPSEEK_ANTHROPIC_BASE_URL
    env: Dict[str, str] = {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_MODEL": primary,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": (
            os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "").strip() or primary
        ),
        "ANTHROPIC_DEFAULT_SONNET_MODEL": (
            os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "").strip() or primary
        ),
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": (
            os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "").strip() or flash
        ),
        "CLAUDE_CODE_SUBAGENT_MODEL": (
            os.getenv("CLAUDE_CODE_SUBAGENT_MODEL", "").strip() or flash
        ),
        "CLAUDE_CODE_EFFORT_LEVEL": (
            os.getenv("CLAUDE_CODE_EFFORT_LEVEL", "").strip() or "max"
        ),
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": (
            os.getenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "").strip()
            or DEEPSEEK_AUTO_COMPACT_WINDOW
        ),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": (
            os.getenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "").strip() or "1"
        ),
        "DISABLE_AUTOUPDATER": os.getenv("DISABLE_AUTOUPDATER", "").strip() or "1",
    }
    return env


def claude_print_argv(cli: str, brief_arg: str) -> list[str]:
    """Headless Claude Code (``--print``), not Kimi ``--prompt``."""
    return [
        cli,
        "--print",
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
        brief_arg,
    ]
