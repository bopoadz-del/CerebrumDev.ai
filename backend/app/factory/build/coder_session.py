"""One-session coder dispatch + owner Pause/Stop control.

The WRITER compiles one brief, then this module hands it to FACTORY_CODE_CLI.
A keyed production Floor must have that binary AND credentials for the
selected coder (Kimi: ``~/.kimi-code/config.toml`` with a usable
``default_model``; DeepSeek: ``DEEPSEEK_API_KEY`` for Claude Code →
DeepSeek V4 Pro) or fail-closed with ``FACTORY_CODE_CLI_UNAVAILABLE`` /
``FACTORY_CODE_CLI_CREDENTIALS_MISSING`` / ``FACTORY_CODE_CLI_NO_MODEL``
BEFORE it claims the coding agent has taken over. A CLI exit of
``No model configured`` is also ``FACTORY_CODE_CLI_NO_MODEL``
(still ``FACTORY_CODE_CLI_FAILED`` honesty).
A 404 / Permission denied on the configured model is
``FACTORY_CODE_CLI_MODEL_DENIED`` (distinct from NO_MODEL). A
``429`` / insufficient-balance / account-suspended exit is
``FACTORY_CODE_CLI_BILLING`` (still ``FACTORY_CODE_CLI_FAILED`` honesty).
When STEP 0 inventory has **zero gaps** (all capabilities REUSE-present),
that named billing/auth miss must not discard the factory-grounded emit +
harvest keep-path (#333/#336/#337). Remaining GENERATE inventory_gaps
then fall through to the factory coder LLM (same gated brief /
``generate_from_compiled_brief`` path the Floor already uses for README
via OpenRouter) — not a new per-capability handle() loop, and not a
claimed ≥2h CLI session. HTTP oneshot is CI-only
(``FACTORY_BRIEF_HTTP_ONESHOT=1``).

Control is a file the Floor writes. The dispatcher polls it: pause waits,
stop terminates the session. Owner eyes are the monitor.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.factory.build.workflow_accept import (
    handler_has_prepared_event_bus_step,
    handler_satisfies_event_bus_contract,
)

logger = logging.getLogger("cerebrumdev.factory.coder_session")

BRIEF_REL = Path("docs") / "coder_brief.md"
LOG_REL = Path("docs") / "coder_session.log"
CONTROL_REL = Path("docs") / "coder_control.json"
RECEIPT_REL = Path("docs") / "coder_receipt.json"

CONTROL_RUN = "run"
CONTROL_PAUSE = "pause"
CONTROL_STOP = "stop"

BRIEF_DISPATCH_ENV = "FACTORY_BRIEF_DISPATCH"
BRIEF_HTTP_ONESHOT_ENV = "FACTORY_BRIEF_HTTP_ONESHOT"
NAMED_BLOCKER_CLI = "FACTORY_CODE_CLI_UNAVAILABLE"
NAMED_BLOCKER_CLI_CREDS = "FACTORY_CODE_CLI_CREDENTIALS_MISSING"
NAMED_BLOCKER_CLI_FAILED = "FACTORY_CODE_CLI_FAILED"
NAMED_BLOCKER_CLI_NO_MODEL = "FACTORY_CODE_CLI_NO_MODEL"
NAMED_BLOCKER_CLI_MODEL_DENIED = "FACTORY_CODE_CLI_MODEL_DENIED"
NAMED_BLOCKER_CLI_BILLING = "FACTORY_CODE_CLI_BILLING"
#: DeepSeek CLI was ready but C-BRIEF never ran, or the run SUCCESS-ed
#: thin templates (stub_rate≈1.0 / written=0) before a real stage-1 wall.
#: Store-complete REUSE/COMPOSE is not a skip (sess_9d0b43c81b2b4620).
NAMED_BLOCKER_CLI_UNUSED = "FACTORY_CODE_CLI_UNUSED"
NAMED_BLOCKER_STOPPED = "CODER_SESSION_STOPPED"
NAMED_BLOCKER_PAUSED = "CODER_SESSION_PAUSED"
CLI_PREFLIGHT_BLOCKERS = frozenset(
    {NAMED_BLOCKER_CLI, NAMED_BLOCKER_CLI_CREDS, NAMED_BLOCKER_CLI_NO_MODEL}
)
#: Named billing/auth misses that still allow factory-grounded REUSE
#: emit + harvest for verified REUSE rows (sess_d5789a91). GENERATE
#: gaps on the same miss use the factory coder LLM second leg.
CLI_AUTH_BILLING_BLOCKERS = frozenset(
    {
        NAMED_BLOCKER_CLI_BILLING,
        NAMED_BLOCKER_CLI_CREDS,
        NAMED_BLOCKER_CLI_NO_MODEL,
        NAMED_BLOCKER_CLI_MODEL_DENIED,
    }
)
#: CLI honesty classes that fall through GENERATE gaps to the factory
#: coder LLM. UNAVAILABLE is included (no binary); generic FAILED only
#: when the detail is a billing miss. NO_MODEL stays fail-closed.
CLI_GENERATE_LLM_FALLTHROUGH_BLOCKERS = frozenset(
    {
        NAMED_BLOCKER_CLI_BILLING,
        NAMED_BLOCKER_CLI_CREDS,
        NAMED_BLOCKER_CLI,
    }
)
KEEP_PATH_FACTORY_GROUNDED_REUSE = "factory_grounded_reuse"
NO_MODEL_CONFIGURED_HINT = "No model configured"

#: Moonshot Open Platform ids for ``[providers.kimi]`` +
#: ``https://api.moonshot.ai/v1``. Kimi Code CLI 0.41 config-files still
#: show managed ``kimi-code/k3`` / ``k3`` (``managed:kimi-code``,
#: ``https://api.kimi.com/coding/v1``) which needs TTY ``/login``. That
#: pair 404s / Permission-denied on a platform key. Docs:
#: https://platform.moonshot.ai/docs/guide/start-using-kimi-api
#: https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files
KIMI_CODE_MODEL_ENV = "KIMI_CODE_MODEL"
KIMI_CODE_MODEL_ID_ENV = "KIMI_CODE_MODEL_ID"
DEFAULT_KIMI_CODE_MODEL = "kimi-k3"
DEFAULT_KIMI_CODE_MODEL_ID = "kimi-k3"
#: Managed /login catalog aliases → Moonshot API (alias, model id).
_MANAGED_TO_MOONSHOT = {
    "kimi-code/k3": (DEFAULT_KIMI_CODE_MODEL, DEFAULT_KIMI_CODE_MODEL_ID),
    "k3": (DEFAULT_KIMI_CODE_MODEL, DEFAULT_KIMI_CODE_MODEL_ID),
    "kimi-code/kimi-for-coding": ("kimi-k2.7-code", "kimi-k2.7-code"),
    "kimi-for-coding": ("kimi-k2.7-code", "kimi-k2.7-code"),
    "kimi-code/kimi-for-coding-highspeed": (
        "kimi-k2.7-code-highspeed",
        "kimi-k2.7-code-highspeed",
    ),
    "kimi-for-coding-highspeed": (
        "kimi-k2.7-code-highspeed",
        "kimi-k2.7-code-highspeed",
    ),
}
_BROKEN_MANAGED_IDS = frozenset(
    {"k3", "kimi-for-coding", "kimi-for-coding-highspeed"}
)
_MODEL_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_DEFAULT_MODEL_RE = re.compile(
    r'(?m)^\s*default_model\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))'
)
_DEFAULT_MODEL_LINE_RE = re.compile(
    r'(?m)^\s*default_model\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)\s*\n?'
)
_MODEL_FIELD_RE = re.compile(
    r'(?m)^\s*model\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))'
)
_MODEL_DENIED_HINTS = (
    "permission denied",
    "model_not_found",
    "invalid model",
    "unknown model",
    "model does not exist",
)
#: Moonshot / Kimi Code CLI billing-auth class (sess_d5789a91 photograph).
_BILLING_HINTS = (
    "insufficient balance",
    "insufficient_balance",
    "insufficient quota",
    "insufficient_quota",
    "account has been suspended",
    "suspended due to insufficient",
    "invalid api key",
    "invalid_api_key",
)

#: One-line operator note. Dashboard clicks stay owner-gated.
OWNER_GATED_CLI_LOG = (
    "FACTORY_CODE_CLI / DEEPSEEK_API_KEY / KIMI_CODE_API_KEY owner-gated "
    "on Render — not claimed set"
)


class CodeCliUnavailable(RuntimeError):
    """Operator/config class: FACTORY_CODE_CLI is not on the factory host."""

    blocker = NAMED_BLOCKER_CLI


class CodeCliCredentialsMissing(CodeCliUnavailable):
    """Selected coder is on PATH but its credentials are missing.

    Kimi: ``~/.kimi-code/config.toml`` absent. DeepSeek:
    ``DEEPSEEK_API_KEY`` unset while DeepSeek is the selected coder.
    """

    blocker = NAMED_BLOCKER_CLI_CREDS


class CodeCliFailed(RuntimeError):
    """CLI ran and exited non-zero. Not a silent template success."""

    blocker = NAMED_BLOCKER_CLI_FAILED


class CodeCliNoModelConfigured(CodeCliUnavailable):
    """No usable default_model (headless /login is not available).

    Preflight class: generate-start / brief dispatch must refuse before
    WRITER takeover. Mid-run CLI ``No model configured`` uses the same
    named blocker (still ``FACTORY_CODE_CLI_FAILED`` honesty).
    """

    blocker = NAMED_BLOCKER_CLI_NO_MODEL


class CodeCliModelDenied(CodeCliFailed):
    """CLI ran but the configured model 404'd / Permission denied."""

    blocker = NAMED_BLOCKER_CLI_MODEL_DENIED


class CodeCliBillingFailed(CodeCliFailed):
    """CLI ran; Moonshot/account billing refused the session.

    Still ``FACTORY_CODE_CLI_FAILED`` honesty — never a ≥2h CLI session.
    Empty-gap REUSE may continue factory-grounded emit + harvest.
    """

    blocker = NAMED_BLOCKER_CLI_BILLING


def brief_dispatch_enabled() -> bool:
    """Default ON. FACTORY_BRIEF_DISPATCH=0 restores per-capability shots.

    DeepSeek-ready C-BRIEF is not optional: leftover ``FACTORY_BRIEF_DISPATCH=0``
    must not send a keyed Claude→DeepSeek Floor through per-capability
    OpenRouter factory-LLM shots.
    """
    raw = os.getenv(BRIEF_DISPATCH_ENV, "1").strip().lower()
    if raw not in {"0", "false", "no", "off"}:
        return True
    return deepseek_cli_ready()


def http_oneshot_enabled() -> bool:
    """CI/dev escape only. Production Floor must not set this."""
    raw = os.getenv(BRIEF_HTTP_ONESHOT_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def brief_requires_cli() -> bool:
    """Keyed brief path must dispatch via FACTORY_CODE_CLI.

    Unkeyed / ``FACTORY_CODER_ENABLED=0`` still uses honest templates.
    ``FACTORY_BRIEF_HTTP_ONESHOT=1`` keeps the CI oneshot contract.
    ``ENV=test`` does not refuse at generate-start unless
    ``FACTORY_BRIEF_REQUIRE_CLI=1`` (the mutation) **or** DeepSeek CLI
    is already ready (claude on PATH + ``DEEPSEEK_API_KEY``). Production
    (``ENV=production``) and keyed non-test hosts fail-closed.

    Leftover ``FACTORY_BRIEF_REQUIRE_CLI=0`` must not treat a ready
    DeepSeek+claude session as optional — that is how sess_b9fbae7
    coded via in-process OpenRouter while health showed claude/deepseek.
    """
    if http_oneshot_enabled():
        return False
    from app.factory.coder import coder_enabled

    if not coder_enabled():
        return False
    if deepseek_cli_ready():
        return True
    if not brief_dispatch_enabled():
        return False
    require = os.getenv("FACTORY_BRIEF_REQUIRE_CLI", "").strip().lower()
    if require in {"0", "false", "no", "off"}:
        return False
    if require in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("ENV", "").strip().lower() != "test"


def cli_unavailable_detail(command: Optional[str] = None) -> str:
    """Named-class operator text. Names the env, not a dashboard click."""
    from app.factory.coder import (
        CODE_CLI_ENV,
        DEEPSEEK_API_KEY_ENV,
        LEGACY_CODE_CLI_ENV,
        code_cli_command,
        factory_code_provider,
    )

    cli = (command or code_cli_command()).strip() or "kimi"
    provider = factory_code_provider()
    creds = (
        f"{DEEPSEEK_API_KEY_ENV} (Claude Code → DeepSeek V4 Pro)"
        if provider == "deepseek"
        else "KIMI_CODE_API_KEY writes ~/.kimi-code/config.toml; "
        "Claude uses its own login unless DeepSeek is selected"
    )
    return (
        f"{NAMED_BLOCKER_CLI}: {cli!r} is not an executable on this host. "
        f"Set {CODE_CLI_ENV} (wins) or {LEGACY_CODE_CLI_ENV} to the agentic "
        "coder binary (`kimi` or `claude`, or an absolute path) and provide "
        f"CLI credentials ({creds}). HTTP oneshot is not a FACTORY_CODE_CLI "
        f"session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. "
        f"{OWNER_GATED_CLI_LOG}."
    )


def kimi_credentials_home() -> Path:
    return Path(os.environ.get("KIMI_CODE_HOME") or (Path.home() / ".kimi-code"))


def kimi_credentials_file() -> Path:
    return kimi_credentials_home() / "config.toml"


def credentials_file_present() -> bool:
    return kimi_credentials_file().is_file()


def cli_requires_kimi_credentials(command: Optional[str] = None) -> bool:
    """Kimi Code authenticates via config.toml. Claude uses its own login.

    Credentials are expected when the resolved command is kimi (default
    name, ``KIMI_CODE_CLI``, or a path whose basename contains ``kimi``).
    DeepSeek (Claude Code + ``DEEPSEEK_API_KEY``) does not use this file.
    """
    from app.factory.coder import code_cli_command, deepseek_coder_selected

    if deepseek_coder_selected(command):
        return False
    cli = (command or code_cli_command()).strip()
    names = [Path(cli).name.lower()] if cli else []
    resolved = resolve_code_cli(cli) if cli else resolve_code_cli()
    if resolved:
        names.append(Path(resolved).name.lower())
    return any("kimi" in name for name in names)


def cli_requires_deepseek_credentials(command: Optional[str] = None) -> bool:
    """DeepSeek V4 Pro authenticates Claude Code via ``DEEPSEEK_API_KEY``."""
    from app.factory.coder import deepseek_coder_selected

    return deepseek_coder_selected(command)


def cli_credentials_ok(command: Optional[str] = None) -> bool:
    if cli_requires_deepseek_credentials(command):
        from app.factory.coder import deepseek_api_key

        return bool(deepseek_api_key())
    if not cli_requires_kimi_credentials(command):
        return True
    return credentials_file_present()


def read_kimi_config_text() -> str:
    dest = kimi_credentials_file()
    if not dest.is_file():
        return ""
    try:
        return dest.read_text(encoding="utf-8")
    except OSError:
        return ""


def config_has_usable_default_model(text: str) -> bool:
    """True when config.toml has default_model and a matching [models] row.

    Kimi Code CLI 0.41 requires the alias in the ``models`` table. Empty,
    whitespace, or managed leftovers (``kimi-code/k3`` / ``k3``) are not
    usable on a headless Moonshot Floor.
    """
    alias = config_default_model(text)
    if not alias:
        return False
    model_id = config_model_id(text, alias)
    if is_broken_managed_model(alias, model_id):
        return False
    if not config_has_model_alias(text, alias):
        return False
    return bool(model_id)


def cli_default_model_ok(command: Optional[str] = None) -> bool:
    """Credentials-only config.toml is not a usable Kimi model."""
    if cli_requires_deepseek_credentials(command):
        from app.factory.code_cli import deepseek_model_configured

        return deepseek_model_configured()
    if not cli_requires_kimi_credentials(command):
        return True
    if not credentials_file_present():
        return True
    return config_has_usable_default_model(read_kimi_config_text())


def deepseek_cli_ready(command: Optional[str] = None) -> bool:
    """True when C-BRIEF must use Claude Code → DeepSeek, not factory LLM.

    Health-ready photograph: ``FACTORY_CODE_CLI=claude`` (or DeepSeek default)
    is an executable, ``DEEPSEEK_API_KEY`` is present, and a DeepSeek coding
    model id is resolvable. HTTP oneshot stays the CI escape.

    When this is true, Floor Approve must dispatch the compiled brief
    through FACTORY_CODE_CLI even if STEP 0 is 100% REUSE/COMPOSE
    (zero GENERATE gaps). Empty ``inventory_gaps`` is not a skip.
    """
    if http_oneshot_enabled():
        return False
    from app.factory.coder import (
        coder_enabled,
        deepseek_api_key,
        deepseek_coder_selected,
    )

    if not coder_enabled():
        return False
    if not deepseek_coder_selected(command):
        return False
    if not deepseek_api_key():
        return False
    if not cli_available(command):
        return False
    if not cli_credentials_ok(command):
        return False
    return cli_default_model_ok(command)


def cli_dispatch_attempted(
    state: Optional[Mapping[str, Any]] = None, ledger: Any = None
) -> bool:
    """True when WRITER actually started FACTORY_CODE_CLI (via=cli)."""
    dispatch = dict((state or {}).get("brief_dispatch") or {})
    if str(dispatch.get("via") or "") == "cli":
        return True
    if ledger is None:
        return False
    for event in getattr(ledger, "events", lambda: ())():
        detail = str(getattr(event, "detail", "") or "")
        payload = getattr(event, "payload", None) or {}
        source = str(payload.get("source") or "")
        if "dispatching compiled brief via FACTORY_CODE_CLI" in detail:
            return True
        if source == "coder CLI":
            return True
    return False


def thin_stub_success_blocked(
    *,
    snapshot: Mapping[str, Any],
    elapsed_s: float,
    state: Optional[Mapping[str, Any]] = None,
    ledger: Any = None,
) -> Optional[str]:
    """Named blocker: DeepSeek CLI ready, thin stubs, no real stage-1 wall.

    sess_9d0b43c81b2b4620: 8s ``budget_inspect`` hard-stop
    ``written=0 templated=4 stub_rate=1.0`` then ``outcome=SUCCESS`` on a
    store-complete lettings golden — C-BRIEF never got the remapped ≥1800s
    wall. Prefer keep-running CLI or fail-closed STOPPED; never thin
    Store-green SUCCESS from pure templates when the CLI was ready.
    """
    if not deepseek_cli_ready():
        return None
    written = int(snapshot.get("agent_written") or 0)
    try:
        stub_rate = float(snapshot.get("stub_rate") or 0.0)
    except (TypeError, ValueError):
        stub_rate = 0.0
    if written > 0 or stub_rate < 0.99:
        return None
    attempted = bool(snapshot.get("cli_attempted")) or cli_dispatch_attempted(
        state, ledger
    )
    from app.factory.build.budget_inspect import STAGE_1_S

    if not attempted:
        return (
            f"{NAMED_BLOCKER_CLI_UNUSED}: DeepSeek FACTORY_CODE_CLI is ready "
            "but C-BRIEF was not dispatched (via≠cli). Store-complete "
            "REUSE/COMPOSE is not a skip — do not SUCCESS thin templates "
            f"(written={written}, stub_rate={stub_rate})."
        )
    if float(elapsed_s) + 1.0 < float(STAGE_1_S):
        return (
            f"{NAMED_BLOCKER_CLI_UNUSED}: DeepSeek FACTORY_CODE_CLI is ready "
            f"but has not had a stage-1 wall ({elapsed_s:.0f}s < "
            f"{int(STAGE_1_S)}s) and authorship is still thin "
            f"(written={written}, stub_rate={stub_rate}). Do not SUCCESS "
            "a Store-green pilot from pure templates."
        )
    return None


def kimi_prompt_model_alias() -> str:
    """Alias for documented ``kimi --model`` on headless ``--prompt``.

    Prefers the file's usable ``default_model``. Falls back to
    ``KIMI_CODE_MODEL`` / Moonshot ``kimi-k3``. The alias must still
    exist under ``[models]`` — preflight refuses when it does not.
    """
    text = read_kimi_config_text()
    alias = config_default_model(text)
    if alias and config_has_usable_default_model(text):
        return alias
    return kimi_code_default_model()


def cli_no_model_detail(command: Optional[str] = None) -> str:
    """Named-class operator text for binary + file, no usable default_model."""
    from app.factory.coder import (
        CODE_CLI_ENV,
        DEFAULT_DEEPSEEK_MODEL,
        code_cli_command,
    )

    cli = (command or code_cli_command()).strip() or "kimi"
    if cli_requires_deepseek_credentials(command):
        return (
            f"{NAMED_BLOCKER_CLI_NO_MODEL}: {cli!r} is an executable on this "
            "host but no DeepSeek coding model is configured. Set "
            f"ANTHROPIC_MODEL / DEEPSEEK_CODE_MODEL (default "
            f"{DEFAULT_DEEPSEEK_MODEL}) for the Claude Code subprocess. "
            f"{CODE_CLI_ENV} / CEREBRUM_LLM_API_KEY do not configure the "
            "DeepSeek coding model. HTTP oneshot is not a FACTORY_CODE_CLI "
            f"session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. A "
            f"templated pilot zip is not a ≥2h CLI session. "
            f"{OWNER_GATED_CLI_LOG}."
        )
    dest = kimi_credentials_file()
    return (
        f"{NAMED_BLOCKER_CLI_NO_MODEL}: {cli!r} is an executable on this host "
        f"and {dest} is present (credentials_file_present=true) but has no "
        "usable default_model / [models] entry. Headless Floor cannot run "
        "`kimi` /login. Set KIMI_CODE_API_KEY so boot writes default_model "
        f"(KIMI_CODE_MODEL, default {DEFAULT_KIMI_CODE_MODEL} / Moonshot API) "
        f"and [models.\"{DEFAULT_KIMI_CODE_MODEL}\"]. {CODE_CLI_ENV} / "
        "CEREBRUM_LLM_API_KEY do not configure the Kimi Code model. HTTP "
        "oneshot is not a FACTORY_CODE_CLI session; set "
        f"{BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. A templated pilot zip is "
        f"not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
    )


def cli_credentials_missing_detail(command: Optional[str] = None) -> str:
    """Named-class operator text for binary-present / credentials-absent."""
    from app.factory.coder import (
        CODE_CLI_ENV,
        DEEPSEEK_API_KEY_ENV,
        DEFAULT_DEEPSEEK_MODEL,
        code_cli_command,
    )

    cli = (command or code_cli_command()).strip() or "kimi"
    if cli_requires_deepseek_credentials(command):
        return (
            f"{NAMED_BLOCKER_CLI_CREDS}: {cli!r} is an executable on this host "
            f"but {DEEPSEEK_API_KEY_ENV} is unset. DeepSeek is the selected "
            "FACTORY_CODE_CLI coder (FACTORY_CODE_PROVIDER=deepseek or "
            f"{DEEPSEEK_API_KEY_ENV} / FACTORY_CODE_CLI=claude). Set "
            f"{DEEPSEEK_API_KEY_ENV} so boot injects the official Claude Code "
            f"Anthropic-compat env (model default {DEFAULT_DEEPSEEK_MODEL}). "
            f"{CODE_CLI_ENV} / CEREBRUM_LLM_API_KEY / ANTHROPIC_API_KEY do "
            "not authenticate DeepSeek. HTTP oneshot is not a "
            f"FACTORY_CODE_CLI session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only "
            f"for CI. {OWNER_GATED_CLI_LOG}."
        )
    dest = kimi_credentials_file()
    return (
        f"{NAMED_BLOCKER_CLI_CREDS}: {cli!r} is an executable on this host but "
        f"{dest} is missing (credentials_file_present=false). "
        "Set KIMI_CODE_API_KEY so boot writes [providers.kimi] and "
        "default_model (KIMI_CODE_MODEL, default kimi-k3 / Moonshot API) into that file "
        f"(or place the file yourself). {CODE_CLI_ENV} / CEREBRUM_LLM_API_KEY "
        "do not authenticate the Kimi Code CLI. HTTP oneshot is not a "
        f"FACTORY_CODE_CLI session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. "
        f"{OWNER_GATED_CLI_LOG}."
    )


def raise_if_cli_session_unready() -> None:
    """Fail-closed before generate-start claims the coding agent took over."""
    if not brief_requires_cli():
        return
    if not cli_available():
        raise CodeCliUnavailable(cli_unavailable_detail())
    if not cli_credentials_ok():
        raise CodeCliCredentialsMissing(cli_credentials_missing_detail())
    if not cli_default_model_ok():
        raise CodeCliNoModelConfigured(cli_no_model_detail())


def coder_artifact_paths(root: Path) -> Dict[str, Path]:
    root = Path(root)
    return {
        "brief": root / BRIEF_REL,
        "log": root / LOG_REL,
        "control": root / CONTROL_REL,
        "receipt": root / RECEIPT_REL,
    }


def write_control(root: Path, action: str) -> Dict[str, Any]:
    """Owner Pause / Stop / Resume. Written outside the role workspace."""
    action = str(action or "").strip().lower()
    if action == "resume":
        action = CONTROL_RUN
    if action not in {CONTROL_RUN, CONTROL_PAUSE, CONTROL_STOP}:
        raise ValueError(f"unknown coder control action: {action!r}")
    payload = {"action": action, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = Path(root) / CONTROL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def read_control(root: Path) -> str:
    path = Path(root) / CONTROL_REL
    if not path.is_file():
        return CONTROL_RUN
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CONTROL_RUN
    action = str((data or {}).get("action") or CONTROL_RUN).strip().lower()
    if action == "resume":
        return CONTROL_RUN
    return action if action in {CONTROL_RUN, CONTROL_PAUSE, CONTROL_STOP} else CONTROL_RUN


def read_log_tail(root: Path, *, max_chars: int = 8000) -> str:
    path = Path(root) / LOG_REL
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def read_receipt(root: Path) -> Dict[str, Any]:
    path = Path(root) / RECEIPT_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def session_status(root: Path) -> Dict[str, Any]:
    """Fields stamped onto build_status for the Floor monitor."""
    control = read_control(root)
    receipt = read_receipt(root)
    brief = Path(root) / BRIEF_REL
    log = Path(root) / LOG_REL
    return {
        "coder_control": control,
        "coder_log": read_log_tail(root),
        "coder_brief_present": brief.is_file(),
        "coder_log_present": log.is_file(),
        "coder_receipt": receipt,
        "brief_dispatch": receipt.get("via") or ("compiled" if brief.is_file() else None),
    }


def _append_log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")


def wait_if_paused(
    root: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 0.25,
    deadline: Optional[float] = None,
) -> str:
    """Block while the owner has PAUSE set. Returns the control action."""
    while True:
        action = read_control(root)
        if action != CONTROL_PAUSE:
            return action
        if deadline is not None and clock() >= deadline:
            return CONTROL_STOP
        sleep(poll_s)


def resolve_code_cli(command: Optional[str] = None) -> Optional[str]:
    """Absolute path or PATH name that ``cli_available`` would accept."""
    from app.factory.coder import code_cli_command

    cli = (command or code_cli_command()).strip()
    if not cli:
        return None
    path = Path(cli).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    found = shutil.which(cli)
    if found:
        return found
    name = path.name if path.name else cli
    extras = [
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
        Path("/app/.local/bin") / name,
        Path.home() / ".local" / "bin" / name,
    ]
    for candidate in extras:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def cli_available(command: Optional[str] = None) -> bool:
    return resolve_code_cli(command) is not None


def probe_code_cli() -> Dict[str, Any]:
    """Health / operator view of FACTORY_CODE_CLI (binary, not workbench flag)."""
    from app.factory.coder import (
        code_cli_command,
        deepseek_api_key,
        deepseek_code_model,
        factory_code_provider,
    )

    command = code_cli_command()
    resolved = resolve_code_cli(command)
    provider = factory_code_provider()
    wants_deepseek = cli_requires_deepseek_credentials(command)
    wants_kimi = cli_requires_kimi_credentials(command)
    kimi_file = credentials_file_present()
    config_text = read_kimi_config_text() if kimi_file else ""
    kimi_alias = config_default_model(config_text) if kimi_file else ""
    model_ok = cli_default_model_ok(command)
    deepseek_key = bool(deepseek_api_key())
    if wants_deepseek:
        default_model = deepseek_code_model()
        default_configured = bool(model_ok)
    else:
        default_model = kimi_alias or None
        default_configured = bool(model_ok) if kimi_file else False
    probe: Dict[str, Any] = {
        "command": command,
        "provider": provider,
        "available": bool(resolved),
        "resolved": resolved,
        "credentials_file_present": kimi_file,
        "deepseek_key_present": deepseek_key,
        "default_model": default_model or None,
        "default_model_configured": default_configured,
        "requires_cli": brief_requires_cli(),
        "requires_kimi_credentials": wants_kimi,
        "requires_deepseek_credentials": wants_deepseek,
    }
    if not resolved:
        probe["blocker"] = NAMED_BLOCKER_CLI
        probe["error"] = cli_unavailable_detail(command)
    elif brief_requires_cli() and not cli_credentials_ok(command):
        probe["blocker"] = NAMED_BLOCKER_CLI_CREDS
        probe["error"] = cli_credentials_missing_detail(command)
    elif brief_requires_cli() and not model_ok:
        probe["blocker"] = NAMED_BLOCKER_CLI_NO_MODEL
        probe["error"] = cli_no_model_detail(command)
    return probe


def _moonshot_alias(name: str) -> str:
    mapped = _MANAGED_TO_MOONSHOT.get(name)
    return mapped[0] if mapped else name


def _moonshot_model_id(name: str) -> str:
    mapped = _MANAGED_TO_MOONSHOT.get(name)
    if mapped:
        return mapped[1]
    return name.rsplit("/", 1)[-1] if name else DEFAULT_KIMI_CODE_MODEL_ID


def kimi_code_model_env_set() -> bool:
    return bool(
        os.getenv(KIMI_CODE_MODEL_ENV, "").strip()
        or os.getenv(KIMI_CODE_MODEL_ID_ENV, "").strip()
    )


def kimi_code_default_model() -> str:
    """Alias written as ``default_model``. ``KIMI_CODE_MODEL`` overrides.

    Managed ``kimi-code/k3`` / ``k3`` (Kimi Code CLI 0.41 complete example)
    map to Moonshot ``kimi-k3`` so a platform key on api.moonshot.ai does
    not 404. Headless Floor cannot ``/login``.
    """
    raw = os.getenv(KIMI_CODE_MODEL_ENV, "").strip()
    if raw and _MODEL_ALIAS_RE.match(raw):
        return _moonshot_alias(raw)
    return DEFAULT_KIMI_CODE_MODEL


def kimi_code_model_id(alias: Optional[str] = None) -> str:
    """API model id for the ``[models]`` table (Moonshot catalog).

    ``KIMI_CODE_MODEL_ID`` wins. Managed leftovers (``k3``) map to
    ``kimi-k3``. Otherwise the last path segment of the alias.
    """
    override = os.getenv(KIMI_CODE_MODEL_ID_ENV, "").strip()
    if override and _MODEL_ALIAS_RE.match(override):
        return _moonshot_model_id(override)
    name = alias or kimi_code_default_model()
    return _moonshot_model_id(name)


def config_default_model(text: str) -> str:
    match = _DEFAULT_MODEL_RE.search(text or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or match.group(3) or "").strip()


def config_has_kimi_provider(text: str) -> bool:
    return "[providers.kimi]" in (text or "")


def _model_table_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    return re.compile(
        rf'(?ms)^\[models\.(?:"{escaped}"|\'{escaped}\'|{escaped})\][^\[]*(?=^\[|\Z)'
    )


def config_has_model_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    return _model_table_pattern(alias).search(text or "") is not None


def config_model_id(text: str, alias: str) -> str:
    """``model =`` field under ``[models."<alias>"]``."""
    if not alias:
        return ""
    block = _model_table_pattern(alias).search(text or "")
    if not block:
        return ""
    match = _MODEL_FIELD_RE.search(block.group(0))
    if not match:
        return ""
    return (match.group(1) or match.group(2) or match.group(3) or "").strip()


def is_broken_managed_model(alias: str, model_id: str = "") -> bool:
    """True for Kimi Code /login catalog names that 404 on Moonshot API."""
    if alias in _MANAGED_TO_MOONSHOT:
        return True
    mid = (model_id or "").rsplit("/", 1)[-1]
    return mid in _BROKEN_MANAGED_IDS


def config_needs_kimi_model_rewrite(
    text: str, alias: str, model_id: str
) -> bool:
    """Whether boot should mutate ``default_model`` / ``[models]``."""
    current = config_default_model(text)
    if not current:
        return True
    current_id = config_model_id(text, current)
    if not config_has_model_alias(text, current):
        return True
    if is_broken_managed_model(current, current_id):
        return True
    if kimi_code_model_env_set() and (
        current != alias or current_id != model_id
    ):
        return True
    return False


def _model_context_size(model_id: str) -> int:
    # Moonshot K3 is 1M; Kimi Code CLI 0.41 k3 example matches. Others 256k.
    tail = (model_id or "").rsplit("/", 1)[-1].lower()
    return 1048576 if tail in {"k3", "kimi-k3"} else 262144


def _provider_block(key: str, base_url: str) -> str:
    return (
        "[providers.kimi]\n"
        'type = "kimi"\n'
        f'api_key = "{key}"\n'
        f'base_url = "{base_url}"\n'
    )


def _model_table_block(alias: str, model_id: str) -> str:
    return (
        f'[models."{alias}"]\n'
        'provider = "kimi"\n'
        f'model = "{model_id}"\n'
        f"max_context_size = {_model_context_size(model_id)}\n"
    )


def _ensure_trailing_newline(text: str) -> str:
    if text and not text.endswith("\n"):
        return text + "\n"
    return text


def _replace_or_append_model_table(
    text: str, alias: str, model_id: str
) -> Tuple[str, bool]:
    block = _model_table_block(alias, model_id)
    pattern = _model_table_pattern(alias)
    if pattern.search(text):
        current_id = config_model_id(text, alias)
        if current_id == model_id:
            return text, False
        return pattern.sub(block.rstrip() + "\n\n", text), True
    out = _ensure_trailing_newline(text)
    if out and not out.endswith("\n\n"):
        out += "\n"
    return out + block, True


def apply_kimi_code_default_model(
    text: str, *, alias: Optional[str] = None, model_id: Optional[str] = None
) -> Tuple[str, bool]:
    """Converge ``default_model`` + ``[models]`` to alias/id. Keeps keys."""
    alias = alias or kimi_code_default_model()
    model_id = model_id or kimi_code_model_id(alias)
    out = text or ""
    current = config_default_model(out)
    mutated = False
    if not current:
        out = re.sub(r"(?m)^\s*default_model\s*=\s*(?:\"\"|''|\s*)\s*\n?", "", out)
        prefix = f'default_model = "{alias}"\n'
        body = out.lstrip("\n")
        out = prefix + ("\n" + body if body else "")
        current = alias
        mutated = True
    elif current != alias:
        out = _DEFAULT_MODEL_LINE_RE.sub(f'default_model = "{alias}"\n', out, count=1)
        out = _model_table_pattern(current).sub("", out)
        current = alias
        mutated = True
    out, table_mutated = _replace_or_append_model_table(out, current, model_id)
    return out, mutated or table_mutated


def classify_cli_exit(code: int, output: str) -> Tuple[str, str]:
    """Named fail-closed class for a non-zero FACTORY_CODE_CLI exit.

    ``FACTORY_CODE_CLI_FAILED`` stays the generic honesty class. A more
    specific ``FACTORY_CODE_CLI_NO_MODEL`` fires when the CLI prints
    ``No model configured`` (headless /login is not a Floor path).
    ``FACTORY_CODE_CLI_MODEL_DENIED`` fires on 404 / Permission denied
    for the configured model (live tip after #324: ``k3`` /
    ``kimi-code/k3`` on Moonshot). ``FACTORY_CODE_CLI_BILLING`` fires on
    Moonshot ``429`` account-suspended / insufficient balance
    (sess_d5789a91). A templated pilot zip is not a ≥2h CLI session.
    """
    exit_bit = f"CLI exited {code}"
    blob = output or ""
    lowered = blob.lower()
    if NO_MODEL_CONFIGURED_HINT in blob:
        return (
            NAMED_BLOCKER_CLI_NO_MODEL,
            (
                f"{NAMED_BLOCKER_CLI_NO_MODEL}: {exit_bit} — No model configured. "
                "Headless Floor cannot run `kimi` /login. Set KIMI_CODE_MODEL / "
                "KIMI_CODE_MODEL_ID "
                f"(default {DEFAULT_KIMI_CODE_MODEL} / "
                f"{DEFAULT_KIMI_CODE_MODEL_ID}, Moonshot API) so boot writes "
                "default_model into ~/.kimi-code/config.toml. A templated "
                f"pilot zip is not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
            ),
        )
    modelish = any(
        token in lowered
        for token in ("model", "k3", "kimi-code", "kimi-k3", "kimi-k2")
    )
    denied = any(hint in lowered for hint in _MODEL_DENIED_HINTS)
    not_found_404 = "404" in lowered and modelish
    if (denied and modelish) or not_found_404:
        return (
            NAMED_BLOCKER_CLI_MODEL_DENIED,
            (
                f"{exit_bit} — configured model 404 / Permission denied. "
                "Headless Floor cannot run `kimi` /login. Set "
                "KIMI_CODE_MODEL / KIMI_CODE_MODEL_ID (default "
                f"{DEFAULT_KIMI_CODE_MODEL} / {DEFAULT_KIMI_CODE_MODEL_ID}, "
                "Moonshot api.moonshot.ai — not managed kimi-code/k3). Boot "
                "rewrites ~/.kimi-code/config.toml. A templated pilot zip "
                f"is not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
            ),
        )
    billing = any(hint in lowered for hint in _BILLING_HINTS)
    rate_suspended = "429" in lowered and "suspended" in lowered
    deepseek_denied = "429" in lowered and any(
        token in lowered
        for token in ("deepseek", "insufficient", "quota", "balance", "billing")
    )
    if billing or rate_suspended or deepseek_denied:
        return (
            NAMED_BLOCKER_CLI_BILLING,
            (
                f"{NAMED_BLOCKER_CLI_BILLING}: {exit_bit} — coder account "
                "billing/auth refused the session (insufficient balance / "
                "429 / DeepSeek or Moonshot). Still FACTORY_CODE_CLI_FAILED "
                "honesty — not a ≥2h CLI session. Verified REUSE continues "
                "factory-grounded emit + harvest; GENERATE inventory_gaps "
                "fall through to the factory coder LLM and stay listed "
                f"until artifacts land. {OWNER_GATED_CLI_LOG}."
            ),
        )
    return NAMED_BLOCKER_CLI_FAILED, exit_bit


def _wire_deepseek_cli_credentials() -> Dict[str, Any]:
    """Validate DeepSeek for Claude Code. Does not mutate process ANTHROPIC_*.

    Official DeepSeek env is applied to the FACTORY_CODE_CLI **subprocess**
    at dispatch (see ``deepseek_cli_environ``). Setting those names
    process-wide would leak into ``LLM_PROVIDER=claude`` Floor chat.
    """
    from app.factory.coder import (
        DEEPSEEK_API_KEY_ENV,
        DEFAULT_DEEPSEEK_MODEL,
        deepseek_api_key,
        deepseek_cli_environ,
        deepseek_code_model,
        deepseek_coder_selected,
        factory_code_provider,
    )

    selected = deepseek_coder_selected() or factory_code_provider() == "deepseek"
    key = deepseek_api_key()
    if not selected and not key:
        return {
            "ok": False,
            "wrote": False,
            "mutated": False,
            "reason": f"{DEEPSEEK_API_KEY_ENV} unset",
            "provider": factory_code_provider(),
        }
    if selected and not key:
        logger.info(OWNER_GATED_CLI_LOG)
        return {
            "ok": False,
            "wrote": False,
            "mutated": False,
            "reason": f"{DEEPSEEK_API_KEY_ENV} unset",
            "provider": "deepseek",
        }
    env = deepseek_cli_environ(key)
    model = env.get("ANTHROPIC_MODEL") or deepseek_code_model() or DEFAULT_DEEPSEEK_MODEL
    logger.info(
        "wired DeepSeek V4 Pro for FACTORY_CODE_CLI subprocess "
        "(model=%s base=%s; ANTHROPIC_* not exported process-wide)",
        model,
        env.get("ANTHROPIC_BASE_URL"),
    )
    return {
        "ok": True,
        "wrote": False,
        "mutated": False,
        "reason": "deepseek wired",
        "provider": "deepseek",
        "model": model,
        "cli": "claude",
        "subprocess_env_keys": sorted(env),
    }


def _merge_cli_credential_result(
    kimi: Dict[str, Any], deepseek: Dict[str, Any]
) -> Dict[str, Any]:
    """Keep the Kimi write shape; surface DeepSeek when it is the live coder."""
    out = dict(kimi)
    out["deepseek"] = dict(deepseek)
    if deepseek.get("ok") and not out.get("ok"):
        out["ok"] = True
        out["reason"] = deepseek.get("reason") or out.get("reason")
        out.setdefault("model", deepseek.get("model"))
        out["provider"] = "deepseek"
    elif (
        not deepseek.get("ok")
        and deepseek.get("provider") == "deepseek"
        and not out.get("ok")
    ):
        out["reason"] = deepseek.get("reason") or out.get("reason")
        out["provider"] = "deepseek"
    elif out.get("ok"):
        out.setdefault("provider", "kimi")
    return out


def ensure_code_cli_credentials() -> Dict[str, Any]:
    """Write ~/.kimi-code/config.toml from KIMI_CODE_API_KEY when present.

    Also writes ``default_model`` (``KIMI_CODE_MODEL`` /
    ``KIMI_CODE_MODEL_ID``, default Moonshot ``kimi-k3``) so
    non-interactive ``kimi --prompt`` works without TTY ``/login``.
    Mutates an existing credentials-only file and a #324
    ``kimi-code/k3`` / ``k3`` file that 404s on api.moonshot.ai. Does
    not install the binary. Does not create a new file when the secret
    is unset — owner-gated stays owner-gated. A credentials-only file
    that already has ``[providers.kimi]`` is still repaired with
    ``default_model`` on the next start. Does not claim the Render
    dashboard is set. Does not claim Floor re-prove.

    When DeepSeek is selected (or ``DEEPSEEK_API_KEY`` is set), also
    validates that key and records the official Claude Code subprocess
    env. Does not set ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_MODEL`` on
    the process (Floor chat stays off DeepSeek).
    """
    deepseek = _wire_deepseek_cli_credentials()
    key = (
        os.getenv("KIMI_CODE_API_KEY", "").strip()
        or os.getenv("KIMI_CODE_KEY", "").strip()
    )
    home = kimi_credentials_home()
    dest = kimi_credentials_file()
    alias = kimi_code_default_model()
    model_id = kimi_code_model_id(alias)
    existed = dest.is_file()
    existing = dest.read_text(encoding="utf-8") if existed else ""
    has_provider = config_has_kimi_provider(existing)
    current_model = config_default_model(existing)
    current_id = config_model_id(existing, current_model)
    rewrite_model = config_needs_kimi_model_rewrite(existing, alias, model_id)
    if not key:
        # Credentials-only file (key already in toml) must still get
        # default_model on next start. Do not create a new file without
        # the owner-gated secret.
        if existed and has_provider and rewrite_model:
            home.mkdir(parents=True, exist_ok=True)
            text, mutated_model = apply_kimi_code_default_model(
                existing, alias=alias, model_id=model_id
            )
            dest.write_text(text, encoding="utf-8")
            written_alias = config_default_model(text) or alias
            written_id = config_model_id(text, written_alias) or model_id
            logger.info(
                "repaired Kimi Code CLI default_model in %s (model=%s id=%s; API key unset)",
                dest,
                written_alias,
                written_id,
            )
            return _merge_cli_credential_result(
                {
                    "ok": True,
                    "wrote": False,
                    "mutated": bool(mutated_model),
                    "path": str(dest),
                    "model": written_alias,
                    "model_id": written_id,
                    "reason": "mutated",
                },
                deepseek,
            )
        logger.info(OWNER_GATED_CLI_LOG)
        return _merge_cli_credential_result(
            {
                "ok": False,
                "wrote": False,
                "mutated": False,
                "reason": "KIMI_CODE_API_KEY unset",
            },
            deepseek,
        )
    if has_provider and current_model and not rewrite_model:
        return _merge_cli_credential_result(
            {
                "ok": True,
                "wrote": False,
                "mutated": False,
                "path": str(dest),
                "model": current_model,
                "model_id": current_id,
                "reason": "already present",
            },
            deepseek,
        )
    home.mkdir(parents=True, exist_ok=True)
    base_url = os.getenv("KIMI_CODE_BASE_URL", "https://api.moonshot.ai/v1").strip()
    text = existing
    wrote_provider = False
    if not has_provider:
        text = _ensure_trailing_newline(text)
        text = text + ("\n" if text else "") + _provider_block(key, base_url)
        wrote_provider = True
    apply_alias = alias
    if (
        current_model
        and not is_broken_managed_model(current_model, current_id)
        and not kimi_code_model_env_set()
    ):
        apply_alias = current_model
    text, mutated_model = apply_kimi_code_default_model(
        text, alias=apply_alias, model_id=kimi_code_model_id(apply_alias)
    )
    dest.write_text(text, encoding="utf-8")
    written_alias = config_default_model(text) or apply_alias
    written_id = config_model_id(text, written_alias) or model_id
    logger.info(
        "wrote Kimi Code CLI credentials to %s (model=%s id=%s; binary still required)",
        dest,
        written_alias,
        written_id,
    )
    return _merge_cli_credential_result(
        {
            "ok": True,
            "wrote": wrote_provider,
            "mutated": bool(existed and mutated_model),
            "path": str(dest),
            "model": written_alias,
            "model_id": written_id,
            "reason": "mutated" if existed and mutated_model else "wrote",
        },
        deepseek,
    )


@dataclass
class DispatchResult:
    via: str
    ok: bool
    detail: str
    specs: Dict[str, Any] = field(default_factory=dict)
    handlers: Dict[str, str] = field(default_factory=dict)
    kept_handler_ids: List[str] = field(default_factory=list)
    model: str = ""
    blocker: Optional[str] = None
    reuse_keep_path: bool = False
    factory_llm_generate_fallthrough: bool = False
    factory_llm_generate_ids: List[str] = field(default_factory=list)
    factory_llm_written_ids: List[str] = field(default_factory=list)
    factory_llm_model: str = ""
    generate_persist_ids: List[str] = field(default_factory=list)
    receipt: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "via": self.via,
            "ok": self.ok,
            "detail": self.detail,
            "model": self.model,
            "blocker": self.blocker,
            "reuse_keep_path": self.reuse_keep_path,
            "factory_llm_generate_fallthrough": self.factory_llm_generate_fallthrough,
            "factory_llm_generate_ids": list(self.factory_llm_generate_ids),
            "factory_llm_written_ids": list(self.factory_llm_written_ids),
            "factory_llm_model": self.factory_llm_model,
            "generate_persist_ids": list(self.generate_persist_ids),
            "handler_ids": sorted(self.handlers),
            "kept_handler_ids": sorted(self.kept_handler_ids),
            "spec_ids": sorted(self.specs),
            "receipt": dict(self.receipt),
        }

    def artifact_source(self, cid: str) -> str:
        """Authorship label for a dispatch-owned spec/handler.

        GENERATE-gap factory-LLM writes credit the factory model
        (OpenRouter / keyed Floor LLM), never FACTORY_CODE_CLI.
        """
        gap_ids = set(self.factory_llm_generate_ids or ())
        written = set(self.factory_llm_written_ids or ())
        if cid in written or (cid in gap_ids and cid in (self.specs or {})):
            return (
                f"coder LLM ({self.factory_llm_model})"
                if self.factory_llm_model
                else "coder LLM (factory)"
            )
        if self.model:
            return f"coder LLM ({self.model})"
        return "compiled-brief oneshot"


def inventory_gap_ids(compiled: Any) -> List[str]:
    """STEP 0 GENERATE/GAP ids. Empty does **not** skip FACTORY_CODE_CLI.

    Store-complete REUSE/COMPOSE still needs the CLI to bind and deepen
    handlers. This list only gates the OpenRouter GENERATE fallthrough
    (#367), not C-BRIEF dispatch.
    """
    out: List[str] = []
    for item in getattr(compiled, "inventory", ()) or ():
        if getattr(item, "is_gap", False):
            cid = str(getattr(item, "capability_id", "") or "").strip()
            if cid:
                out.append(cid)
    return out


def reuse_inventory_ids(compiled: Any) -> List[str]:
    """Verified REUSE rows — factory-grounded emit may keep these."""
    out: List[str] = []
    for item in getattr(compiled, "inventory", ()) or ():
        if getattr(item, "is_gap", False):
            continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if cid:
            out.append(cid)
    return out


def remaining_inventory_gaps(compiled: Any, result: DispatchResult) -> List[str]:
    """Gaps still open after factory-LLM writes land. Fail-closed until then."""
    landed = set(result.factory_llm_written_ids or ()) | set(
        getattr(result, "generate_persist_ids", None) or ()
    )
    return [cid for cid in inventory_gap_ids(compiled) if cid not in landed]


def cli_miss_allows_generate_llm(result: DispatchResult) -> bool:
    """Billing / credentials / unavailable honesty — factory LLM may write gaps."""
    if result.ok or result.via == "http_oneshot":
        return False
    if result.blocker in CLI_GENERATE_LLM_FALLTHROUGH_BLOCKERS:
        return True
    if result.blocker == NAMED_BLOCKER_CLI_FAILED:
        blob = (result.detail or "").lower()
        return any(hint in blob for hint in _BILLING_HINTS) or (
            "429" in blob and "suspended" in blob
        )
    return False


def should_factory_llm_generate_gaps(
    compiled: Any, result: DispatchResult
) -> bool:
    """Named CLI miss + remaining GENERATE gaps → one factory-LLM brief shot.

    A ready DeepSeek+claude session must not fall through to OpenRouter
    (sess_b9fbae7 photographed in-process minimax-m3:free while health
    already showed command=claude / provider=deepseek).

    Empty ``inventory_gaps`` (all REUSE/COMPOSE) must not be treated as
    "skip CLI" — that is a dispatch decision, not this fallthrough.
    """
    if deepseek_cli_ready():
        return False
    if not cli_miss_allows_generate_llm(result):
        return False
    return bool(inventory_gap_ids(compiled))


def should_keep_factory_grounded_reuse(compiled: Any, result: DispatchResult) -> bool:
    """Verified REUSE + named billing/auth CLI miss → keep emit/harvest.

    Live sess_d5789a91 (VetCare Hub / veterinary-care): inventory_gaps=[]
    and FACTORY_CODE_CLI exited 1 with Moonshot 429 / insufficient
    balance. Harvest was skipped because ``result.ok`` was false, then
    WRITER labeled persist/event_bus emit as a deterministic template and
    budget_inspect hard-stopped at stub_rate≈0.833 / SCAFFOLD.

    Mixed plans (REUSE + GENERATE, live veterinary_care_core gap) still
    harvest the REUSE rows. GENERATE gaps are a separate factory-LLM leg.
    GENERATE-only plans do not claim this keep-path.
    """
    if result.ok:
        return False
    if result.blocker not in CLI_AUTH_BILLING_BLOCKERS:
        return False
    return bool(reuse_inventory_ids(compiled))


def factory_grounded_source_for(
    capability_id: str, block_ids: Optional[Sequence[str]] = None
) -> str:
    """Authorship label for factory-grounded persist / event_bus emit."""
    from app.factory.build.persist_accept import FACTORY_GROUNDED_PERSIST_SOURCE
    from app.factory.build.workflow_accept import needs_grounded_event_bus_handler

    if needs_grounded_event_bus_handler(capability_id, block_ids):
        from app.factory.build.workflow_accept import (
            FACTORY_GROUNDED_EVENT_BUS_SOURCE,
        )

        return FACTORY_GROUNDED_EVENT_BUS_SOURCE
    return FACTORY_GROUNDED_PERSIST_SOURCE


def emit_factory_grounded_reuse_keep_path(
    root: Path, compiled: Any, *, only_missing: bool = False
) -> List[str]:
    """Write persist / event_bus handlers for REUSE caps, then harvest can keep.

    Does not claim a CLI session. Does not write GENERATE/GAP caps — those
    fall through to the factory coder LLM after a billing/auth miss.
    ``only_missing`` fills holes after a partial CLI write without
    overwriting a keepable handler.
    """
    from app.factory.build.reuse_accept import harvest_block_default_actions
    from app.factory.build.roles_handlers import (
        _capability_handler_body,
        _handler_module,
    )

    root = Path(root)
    written: List[str] = []
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    for item in getattr(compiled, "inventory", ()) or ():
        if getattr(item, "is_gap", False):
            continue
        if not getattr(item, "handler_source", "") and not getattr(
            item, "verified_present", None
        ):
            continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if not cid:
            continue
        bids = [
            str(b)
            for b in (
                getattr(item, "verified_present", None)
                or getattr(item, "block_ids", None)
                or ()
            )
            if str(b).strip()
        ]
        name = cid.replace("-", "_")
        path = actions / f"{name}.py"
        if only_missing and path.is_file():
            written.append(cid)
            continue
        body = _capability_handler_body(cid, bids)
        source = factory_grounded_source_for(cid, bids)
        defaults = harvest_block_default_actions(bids, root)
        path.write_text(
            _handler_module(
                cid, bids, body, source, defaults, entity=name
            ),
            encoding="utf-8",
        )
        written.append(cid)
    return written


def apply_factory_llm_generate_gaps(
    ctx: Any, compiled: Any, result: DispatchResult
) -> DispatchResult:
    """Second leg: one factory-LLM compiled-brief shot for GENERATE gaps.

    Receipt stays ``ok=false`` with the CLI billing/auth blocker. Not
    ``FACTORY_BRIEF_HTTP_ONESHOT`` and not a ≥2h CLI session. GENERATE
    handlers are persist-grounded on disk (alembic entity +
    ``_persist_record``) even when the factory LLM is empty, raises, or
    keys a body under an alias (sess_336246 ``veterinary_care_core`` vs
    ``vetcare_hub_veterinary_core``).
    """
    if not should_factory_llm_generate_gaps(compiled, result):
        return result
    from app.factory.build.persist_accept import (
        bind_generate_artifacts,
        emit_factory_grounded_generate_persist,
    )
    from app.factory.coder import CoderError, coder_enabled, generate_from_compiled_brief

    if not coder_enabled():
        # Unkeyed / coder-disabled still uses honest templates. Persist
        # emit is the billing keep-path after a factory-LLM attempt.
        return result
    gap_ids = inventory_gap_ids(compiled)
    result.factory_llm_generate_fallthrough = True
    result.factory_llm_generate_ids = list(gap_ids)
    root = _workspace_root(ctx)
    brief = compiled.text if hasattr(compiled, "text") else str(compiled)
    suffix = (
        "\n\n## GENERATE-GAP FALLTHROUGH\n"
        "FACTORY_CODE_CLI missed (billing/credentials/unavailable). "
        f"Write ONLY these GENERATE inventory_gaps: {gap_ids!r}. "
        "Do not claim a ≥2h CLI session. Do not invent REUSE block ids.\n"
    )
    ctx.note(
        (
            f"FACTORY_CODE_CLI {result.blocker} — factory coder LLM for "
            f"{len(gap_ids)} GENERATE gap(s); not a ≥2h CLI session"
        ),
        stage="dispatch",
        source="factory coder LLM (GENERATE gaps)",
        model_call=True,
        done=0,
        total=1,
    )
    llm: Dict[str, Any] = {}
    try:
        llm = generate_from_compiled_brief(
            brief=brief + suffix,
            capabilities=list(gap_ids),
            product_name=compiled.product_name,
            vertical=compiled.vertical,
        )
    except CoderError as exc:
        _append_log(
            root / LOG_REL,
            f"[factory-llm] GENERATE-gap fallthrough after {result.blocker} "
            f"failed: {exc}",
        )
        ctx.state.setdefault("coder_failures", {})["factory_llm_generate"] = str(exc)
        ctx.note(
            f"factory coder LLM GENERATE fallthrough failed: {exc}",
            stage="dispatch",
            source="factory coder LLM (GENERATE gaps)",
            done=0,
            total=1,
        )
    model = str(llm.get("model") or "")
    result.factory_llm_model = model
    written: List[str] = []
    raw_specs = bind_generate_artifacts(gap_ids, llm.get("specs") or {})
    raw_handlers = bind_generate_artifacts(gap_ids, llm.get("handlers") or {})
    for cid in gap_ids:
        spec = raw_specs.get(cid)
        if isinstance(spec, dict) and spec.get("entity"):
            result.specs[cid] = spec
        body = raw_handlers.get(cid)
        if isinstance(body, str) and body.strip():
            result.handlers[cid] = body
            written.append(cid)
    result.factory_llm_written_ids = written
    if written and model:
        persist_source = f"coder LLM ({model})"
    elif written:
        persist_source = "coder LLM (factory)"
    else:
        persist_source = "factory-grounded persist"
    landed = emit_factory_grounded_generate_persist(
        root,
        compiled,
        handlers=result.handlers,
        specs=result.specs,
        source=persist_source,
    )
    result.generate_persist_ids = list(landed)
    _append_log(
        root / LOG_REL,
        "[factory-llm] persist-grounded GENERATE emit "
        f"after {result.blocker}: {landed}",
    )
    _append_log(
        root / LOG_REL,
        f"[factory-llm] GENERATE-gap fallthrough after {result.blocker}: "
        f"attempted={gap_ids} written={written} persist={landed} model={model}",
    )
    ctx.note(
        (
            f"{result.blocker} — factory coder LLM wrote {len(written)}/"
            f"{len(gap_ids)} GENERATE gap(s); persist emit {len(landed)}; "
            "not a ≥2h CLI session"
        ),
        stage="dispatch",
        source=(
            f"coder LLM ({model})" if model else "factory coder LLM (GENERATE gaps)"
        ),
        done=1 if landed else 0,
        total=1,
    )
    return result


def write_dispatch_receipt(
    ctx: Any,
    compiled: Any,
    result: DispatchResult,
) -> Dict[str, Any]:
    """Persist coder_receipt.json. Honesty fields stay even on keep-path."""
    receipt = {
        "via": result.via,
        "ok": result.ok,
        "detail": result.detail,
        "blocker": result.blocker,
        "honesty_class": (
            NAMED_BLOCKER_CLI_FAILED
            if result.blocker
            in CLI_AUTH_BILLING_BLOCKERS | {NAMED_BLOCKER_CLI_FAILED}
            else result.blocker
        ),
        "keep_path": (
            KEEP_PATH_FACTORY_GROUNDED_REUSE if result.reuse_keep_path else None
        ),
        "reuse_keep_path": result.reuse_keep_path,
        "factory_llm_generate_fallthrough": result.factory_llm_generate_fallthrough,
        "factory_llm_generate_ids": list(result.factory_llm_generate_ids),
        "factory_llm_written_ids": list(result.factory_llm_written_ids),
        "factory_llm_model": result.factory_llm_model,
        "generate_persist_ids": list(result.generate_persist_ids),
        "model": result.model,
        "product_id": compiled.product_id,
        "vertical": compiled.vertical,
        "capabilities": list(compiled.capabilities),
        "inventory_reuse": [
            item.capability_id
            for item in compiled.inventory
            if item.verified_present and not item.missing
        ],
        "inventory_gaps": remaining_inventory_gaps(compiled, result),
        "harvested_spec_ids": sorted(result.specs),
        "kept_handler_ids": [
            cid
            for cid in result.kept_handler_ids
            if cid not in set(result.factory_llm_generate_ids or ())
        ],
    }
    result.kept_handler_ids = list(receipt["kept_handler_ids"])
    result.receipt = receipt
    root = _workspace_root(ctx)
    try:
        ctx.workspace.write_text(
            RECEIPT_REL,
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        )
    except Exception:  # noqa: BLE001 — receipt must not fail the role
        _write_receipt(root, receipt)
    return receipt


def refresh_receipt_harvest(
    ctx: Any, compiled: Any, result: DispatchResult
) -> DispatchResult:
    """Re-harvest after WRITER emit so models/handlers land on the receipt."""
    root = _workspace_root(ctx)
    _merge_workspace_harvest(result, root, list(compiled.capabilities))
    write_dispatch_receipt(ctx, compiled, result)
    return result


def _write_receipt(root: Path, payload: Mapping[str, Any]) -> None:
    path = Path(root) / RECEIPT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _workspace_root(ctx: Any) -> Path:
    ws = getattr(ctx, "workspace", None)
    if ws is None:
        raise RuntimeError("dispatch requires a role workspace")
    return Path(getattr(ws, "workspace", ws))


def write_brief_artifacts(ctx: Any, compiled: Any) -> None:
    """Persist the compiled brief + a running control file (WRITER lanes)."""
    text = compiled.text if hasattr(compiled, "text") else str(compiled)
    ctx.workspace.write_text(BRIEF_REL, text if text.endswith("\n") else text + "\n")
    if not (Path(_workspace_root(ctx)) / CONTROL_REL).is_file():
        ctx.workspace.write_text(
            CONTROL_REL,
            json.dumps({"action": CONTROL_RUN, "updated_at": "start"}) + "\n",
        )
    ctx.workspace.write_text(LOG_REL, "")
    ctx.note(
        "compiled one gated brief (TARGET / STEP 0 INVENTORY / DO / ACCEPTANCE)",
        stage="brief",
        done=1,
        total=1,
        source="brief compiler",
    )


def _run_cli_session(
    ctx: Any,
    compiled: Any,
    *,
    timeout_s: float,
) -> DispatchResult:
    from app.factory.coder import (
        claude_print_argv,
        code_cli_command,
        deepseek_cli_environ,
        deepseek_coder_selected,
        is_claude_code_cli,
    )

    root = _workspace_root(ctx)
    log_path = root / LOG_REL
    cli = resolve_code_cli() or code_cli_command()
    brief_arg = f"@{BRIEF_REL.as_posix()}"
    if is_claude_code_cli(cli) or deepseek_coder_selected(cli):
        cmd = claude_print_argv(cli, brief_arg)
    else:
        cmd = [cli, "--prompt", brief_arg, "--add-dir", "."]
        # Kimi Code CLI 0.41 documents ``-m`` / ``--model`` on ``--prompt``
        # (https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command.html).
        # Headless Floor must not depend solely on config.toml mutation.
        if cli_requires_kimi_credentials(cli):
            alias = kimi_prompt_model_alias()
            if alias and _MODEL_ALIAS_RE.match(alias):
                cmd.extend(["--model", alias])
    session_env = os.environ.copy()
    if deepseek_coder_selected(cli):
        session_env.update(deepseek_cli_environ())
    _append_log(log_path, f"$ {' '.join(cmd)}")
    logger.info(
        "FACTORY_CODE_CLI C-BRIEF dispatch via=cli command=%s "
        "gaps=%s reuse=%s timeout_s=%.0f",
        cli,
        inventory_gap_ids(compiled),
        reuse_inventory_ids(compiled),
        timeout_s,
    )
    ctx.note(
        f"dispatching compiled brief via FACTORY_CODE_CLI ({cli})",
        stage="dispatch",
        model_call=True,
        source="coder CLI",
        done=0,
        total=1,
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=session_env,
        )
    except FileNotFoundError:
        return DispatchResult(
            via="unavailable",
            ok=False,
            detail=f"{cli} not found on PATH",
            blocker=NAMED_BLOCKER_CLI,
        )

    deadline = time.monotonic() + timeout_s if timeout_s > 0 else None
    stopped = False
    try:
        while True:
            action = wait_if_paused(root, deadline=deadline)
            if action == CONTROL_STOP:
                stopped = True
                _append_log(log_path, "[owner STOP]")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            if deadline is not None and time.monotonic() >= deadline:
                _append_log(log_path, "[budget wall — stopping CLI session]")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            if proc.poll() is not None:
                break
            if proc.stdout is None:
                time.sleep(0.1)
                continue
            line = proc.stdout.readline()
            if line:
                _append_log(log_path, line.rstrip("\n"))
                ctx.note(line.strip()[:200], stage="dispatch", source="coder CLI")
            else:
                time.sleep(0.05)
        # Drain remainder.
        if proc.stdout is not None:
            rest = proc.stdout.read() or ""
            if rest.strip():
                _append_log(log_path, rest.rstrip("\n"))
        code = proc.wait(timeout=2) if proc.poll() is None else proc.returncode
    except Exception as exc:  # noqa: BLE001
        if proc.poll() is None:
            proc.kill()
        return DispatchResult(
            via="cli",
            ok=False,
            detail=f"CLI session crashed: {exc}",
            blocker="CODER_SESSION_CRASHED",
        )

    if stopped or read_control(root) == CONTROL_STOP:
        return DispatchResult(
            via="cli",
            ok=False,
            detail="owner stopped the coder session",
            blocker=NAMED_BLOCKER_STOPPED,
        )
    if code != 0:
        log_text = ""
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        blocker, detail = classify_cli_exit(int(code or 1), log_text)
        return DispatchResult(
            via="cli",
            ok=False,
            detail=detail,
            blocker=blocker,
            model=cli,
        )
    ctx.note(
        "FACTORY_CODE_CLI session finished",
        stage="dispatch",
        source="coder CLI",
        done=1,
        total=1,
    )
    return DispatchResult(
        via="cli",
        ok=True,
        detail=f"CLI session completed ({cli})",
        model=cli,
    )


def _http_oneshot(ctx: Any, compiled: Any) -> DispatchResult:
    """One HTTP call for the whole job when the CLI is not installed.

    Still not a per-capability micro-loop. Stubbed in CI.
    """
    from app.factory.coder import CoderError, coder_enabled, generate_from_compiled_brief

    if not coder_enabled():
        return DispatchResult(
            via="skipped",
            ok=False,
            detail="coder disabled — templates will author the workspace",
        )
    ctx.note(
        "FACTORY_CODE_CLI unavailable — one HTTP oneshot of the compiled brief",
        stage="dispatch",
        source="coder LLM",
        model_call=True,
        done=0,
        total=1,
    )
    try:
        result = generate_from_compiled_brief(
            brief=compiled.text,
            capabilities=list(compiled.capabilities),
            product_name=compiled.product_name,
            vertical=compiled.vertical,
        )
    except CoderError as exc:
        return DispatchResult(
            via="http_oneshot",
            ok=False,
            detail=str(exc),
            blocker="BRIEF_HTTP_ONESHOT_FAILED",
        )
    ctx.note(
        "compiled-brief HTTP oneshot returned artifacts",
        stage="dispatch",
        source=f"coder LLM ({result.get('model')})",
        done=1,
        total=1,
    )
    return DispatchResult(
        via="http_oneshot",
        ok=True,
        detail="one HTTP oneshot of the compiled brief",
        specs=dict(result.get("specs") or {}),
        handlers=dict(result.get("handlers") or {}),
        model=str(result.get("model") or ""),
    )


_WORKFLOW_STEP_TOKENS = (
    '"steps"',
    "'steps'",
    "steps =",
    'execute("workflow"',
    "execute('workflow'",
    'execute("event_bus"',
    "execute('event_bus'",
    '"block": "event_bus"',
    "'block': 'event_bus'",
    '"block_id": "event_bus"',
    "'block_id': 'event_bus'",
)


def _has_brief_workflow_steps(text: str) -> bool:
    """True when a handler constructs workflow / event_bus steps."""
    blob = text or ""
    if "event_bus" not in blob and "workflow" not in blob:
        return False
    return any(token in blob for token in _WORKFLOW_STEP_TOKENS)


def _is_keepable_handler(text: str) -> bool:
    """CLI / oneshot wrote a complete capability module, not a fragment.

    Prepared brief-driven event_bus steps must survive the fallback
    envelope even when the module omitted CAPABILITY_ID. Unprepared
    ``{'block': 'event_bus', 'input': payload}`` must NOT be kept —
    including an unprepared step_1 (appointment_scheduling after #325)
    and a prepared step_1 plus an unprepared step_2
    (appointment_booking class after #323). A factory wrap around raw
    children is not keepable. That is how PRODUCT
    ``workflow: step_1 (event_bus): error`` locked in
    (sess_14e690829d1f4282).
    """
    blob = text or ""
    if "def handle(" not in blob:
        return False
    if not handler_satisfies_event_bus_contract(
        blob, require_prepared_step=_has_brief_workflow_steps(blob)
    ):
        return False
    if "CAPABILITY_ID" in blob:
        return True
    return handler_has_prepared_event_bus_step(blob) or _has_brief_workflow_steps(blob)


def _merge_workspace_harvest(
    result: DispatchResult,
    root: Path,
    capability_ids: Sequence[str],
) -> None:
    """Keep workspace specs/handlers; do not let the envelope overwrite them."""
    harvested_specs, kept = harvest_cli_artifacts(root, capability_ids)
    if harvested_specs:
        result.specs.update(harvested_specs)
    merged = list(dict.fromkeys([*result.kept_handler_ids, *kept]))
    result.kept_handler_ids = merged
    if harvested_specs or kept:
        _append_log(
            root / LOG_REL,
            "[harvest] workspace "
            f"specs={sorted(harvested_specs)} kept_handlers={merged}",
        )
    # Same-session CLI: prefer on-disk PREPARED workflow steps over a
    # thin / unprepared JSON body. Unprepared disk steps are not kept —
    # the factory wrapper must run prepare_block_input. Do not do this
    # for HTTP oneshot — leftover files from a red PRODUCT round would
    # pin the failing handler and block rework.
    if result.via != "cli":
        return
    for cid in list(result.handlers):
        name = str(cid).replace("-", "_")
        path = Path(root) / "app" / "actions" / f"{name}.py"
        if not path.is_file():
            continue
        try:
            disk = path.read_text(encoding="utf-8")
        except OSError:
            continue
        body = result.handlers.get(cid) or ""
        disk_prepared = handler_satisfies_event_bus_contract(
            disk
        ) and (
            handler_has_prepared_event_bus_step(disk)
            or _has_brief_workflow_steps(disk)
        )
        body_prepared = handler_has_prepared_event_bus_step(body)
        if disk_prepared and not body_prepared:
            result.handlers.pop(cid, None)
            if cid not in result.kept_handler_ids:
                result.kept_handler_ids.append(cid)


def specs_from_models_source(text: str) -> Dict[str, Any]:
    """Read MODELS / FIELDS / CONSTRAINTS the CLI (or factory) wrote.

    Used so FACTORY_CODE_CLI domain specs are not discarded in favour of
    the fallback envelope — that mismatch is how every capability then
    refused a payload built from the overwritten model.
    """
    if not (text or "").strip():
        return {}
    ns: Dict[str, Any] = {}
    try:
        exec(compile(text, "models.py", "exec"), ns)  # noqa: S102 — workspace artifact
    except Exception:  # noqa: BLE001 — harvest must not fail the role
        return {}
    models = ns.get("MODELS")
    if not isinstance(models, dict):
        return {}
    out: Dict[str, Any] = {}
    for cap_id, cls in models.items():
        names = list(getattr(cls, "FIELDS", None) or [])
        constraints = dict(getattr(cls, "CONSTRAINTS", None) or {})
        fields = []
        for name in names:
            field: Dict[str, Any] = {
                "name": str(name),
                "type": "str",
                "required": True,
            }
            extra = constraints.get(name)
            if isinstance(extra, dict):
                field.update(extra)
            fields.append(field)
        entity = getattr(cls, "ENTITY", None) or str(cap_id)
        out[str(cap_id)] = {
            "entity": str(entity).replace("-", "_"),
            "fields": fields,
            "model": None,
        }
    return out


def harvest_cli_artifacts(
    root: Path,
    capability_ids: Sequence[str],
) -> tuple:
    """Collect CLI-written specs + keepable handler ids from the workspace."""
    root = Path(root)
    specs: Dict[str, Any] = {}
    models_py = root / "app" / "models.py"
    if models_py.is_file():
        try:
            specs = specs_from_models_source(
                models_py.read_text(encoding="utf-8")
            )
        except OSError:
            specs = {}
    kept: List[str] = []
    for cid in capability_ids:
        name = str(cid).replace("-", "_")
        path = root / "app" / "actions" / f"{name}.py"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_keepable_handler(text):
            kept.append(str(cid))
    return specs, kept


def dispatch_compiled_brief(ctx: Any, compiled: Any) -> DispatchResult:
    """Hand the compiled brief to the agentic coder. One session.

    DeepSeek-ready Floor Approve always starts FACTORY_CODE_CLI here,
    including inventories that are 100% REUSE/COMPOSE (no GENERATE gaps).
    """
    root = _workspace_root(ctx)
    timeout_s = 1500.0
    left = ctx.coder_time_left() if hasattr(ctx, "coder_time_left") else None
    if left is not None:
        timeout_s = max(30.0, float(left) - 15.0)
    if deepseek_cli_ready():
        from app.factory.build.budget_inspect import STAGE_1_S

        # Honour remapped ≥1800s from #367 even when leftover time is tiny.
        timeout_s = max(timeout_s, float(STAGE_1_S) - 15.0)
        if left is not None and 0 < float(left) <= 600.0:
            timeout_s = max(timeout_s, float(STAGE_1_S) - 15.0)
        logger.info(
            "FACTORY_CODE_CLI C-BRIEF dispatch starting command=%s "
            "gaps=%s reuse=%s timeout_s=%.0f",
            resolve_code_cli() or "",
            inventory_gap_ids(compiled),
            reuse_inventory_ids(compiled),
            timeout_s,
        )

    if cli_available() and brief_requires_cli() and not cli_credentials_ok():
        detail = cli_credentials_missing_detail()
        logger.error("%s", OWNER_GATED_CLI_LOG)
        _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI_CREDS}] {detail}")
        ctx.note(
            f"{NAMED_BLOCKER_CLI_CREDS} — coding session never opened",
            stage="dispatch",
            source="brief dispatch",
            done=0,
            total=1,
        )
        result = DispatchResult(
            via="unavailable",
            ok=False,
            detail=detail,
            blocker=NAMED_BLOCKER_CLI_CREDS,
        )
    elif cli_available() and brief_requires_cli() and not cli_default_model_ok():
        detail = cli_no_model_detail()
        logger.error("%s", OWNER_GATED_CLI_LOG)
        _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI_NO_MODEL}] {detail}")
        ctx.note(
            f"{NAMED_BLOCKER_CLI_NO_MODEL} — coding session never opened",
            stage="dispatch",
            source="brief dispatch",
            done=0,
            total=1,
        )
        result = DispatchResult(
            via="unavailable",
            ok=False,
            detail=detail,
            blocker=NAMED_BLOCKER_CLI_NO_MODEL,
        )
    elif cli_available():
        result = _run_cli_session(ctx, compiled, timeout_s=timeout_s)
        if result.ok:
            # #318 keep-path: prefer on-disk workflow/event_bus steps over a
            # thin JSON body so the fallback envelope cannot overwrite them.
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
            # Partial CLI (live ~2/5) must not leave REUSE routes importing
            # missing app.actions modules. Fill holes only; do not flip
            # reuse_keep_path (receipt stays CLI-ok).
            filled = emit_factory_grounded_reuse_keep_path(
                root, compiled, only_missing=True
            )
            if filled:
                _merge_workspace_harvest(
                    result, root, list(compiled.capabilities)
                )
        elif should_keep_factory_grounded_reuse(compiled, result):
            # sess_d5789a91: CLI 429 / insufficient balance with empty
            # inventory_gaps. Harvest used to run only on result.ok, so
            # factory-grounded persist / event_bus emit was discarded and
            # budget_inspect hard-stopped as a thin SCAFFOLD.
            emitted = emit_factory_grounded_reuse_keep_path(root, compiled)
            result.reuse_keep_path = bool(emitted)
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
            _append_log(
                root / LOG_REL,
                "[harvest] factory-grounded REUSE keep-path after "
                f"{result.blocker}: emitted={emitted} "
                f"kept={list(result.kept_handler_ids)}",
            )
            ctx.note(
                (
                    f"{result.blocker} — factory-grounded REUSE keep-path "
                    f"({len(result.kept_handler_ids)} handler(s)); "
                    "not a ≥2h CLI session"
                ),
                stage="dispatch",
                source="factory-grounded reuse keep-path",
                done=1 if result.kept_handler_ids else 0,
                total=1,
            )
    elif http_oneshot_enabled():
        _append_log(
            root / LOG_REL,
            f"[{NAMED_BLOCKER_CLI}] {BRIEF_HTTP_ONESHOT_ENV}=1 — "
            "HTTP oneshot (CI/dev escape, not a FACTORY_CODE_CLI session)",
        )
        result = _http_oneshot(ctx, compiled)
        if result.via == "skipped":
            result.blocker = NAMED_BLOCKER_CLI
        elif result.ok:
            # Harvest only a successful oneshot. A skipped / failed shot
            # must not treat the previous round's files as "kept" or a
            # rework pass cannot regenerate the failing capability.
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
    else:
        from app.factory.coder import coder_enabled

        if not coder_enabled():
            _append_log(
                root / LOG_REL,
                f"[{NAMED_BLOCKER_CLI}] coder disabled — templates will author the workspace",
            )
            result = DispatchResult(
                via="skipped",
                ok=False,
                detail="coder disabled — templates will author the workspace",
                blocker=NAMED_BLOCKER_CLI,
            )
        else:
            detail = cli_unavailable_detail()
            logger.error("%s", OWNER_GATED_CLI_LOG)
            _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI}] {detail}")
            ctx.note(
                f"{NAMED_BLOCKER_CLI} — coding session never opened",
                stage="dispatch",
                source="brief dispatch",
                done=0,
                total=1,
            )
            result = DispatchResult(
                via="unavailable",
                ok=False,
                detail=detail,
                blocker=NAMED_BLOCKER_CLI,
            )

    if (
        not result.ok
        and not result.reuse_keep_path
        and should_keep_factory_grounded_reuse(compiled, result)
    ):
        emitted = emit_factory_grounded_reuse_keep_path(root, compiled)
        result.reuse_keep_path = bool(emitted)
        _merge_workspace_harvest(result, root, list(compiled.capabilities))
        _append_log(
            root / LOG_REL,
            "[harvest] factory-grounded REUSE keep-path after "
            f"{result.blocker}: emitted={emitted} "
            f"kept={list(result.kept_handler_ids)}",
        )

    apply_factory_llm_generate_gaps(ctx, compiled, result)
    write_dispatch_receipt(ctx, compiled, result)
    ctx.state["brief_dispatch"] = result.to_dict()
    ctx.state["compiled_brief"] = {
        "product_id": compiled.product_id,
        "vertical": compiled.vertical,
        "missing_reuse": list(compiled.missing_reuse),
        "capabilities": list(compiled.capabilities),
    }
    if result.blocker:
        ctx.state.setdefault("coder_failures", {})["brief_dispatch"] = (
            f"{result.blocker}: {result.detail}"
        )
    return result
