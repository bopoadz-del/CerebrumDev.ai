"""One-session coder dispatch + owner Pause/Stop control.

The WRITER compiles one brief, then this module hands it to FACTORY_CODE_CLI.
A keyed production Floor must have that binary AND, for Kimi, a credentials
file (or fail-closed with ``FACTORY_CODE_CLI_UNAVAILABLE`` /
``FACTORY_CODE_CLI_CREDENTIALS_MISSING``) BEFORE it claims the coding agent
has taken over. A CLI exit of ``No model configured`` is
``FACTORY_CODE_CLI_NO_MODEL`` (still ``FACTORY_CODE_CLI_FAILED`` honesty).
A templated pilot zip after that skip is not a ≥2h CLI session. HTTP
oneshot is CI-only (``FACTORY_BRIEF_HTTP_ONESHOT=1``).

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
NAMED_BLOCKER_STOPPED = "CODER_SESSION_STOPPED"
NAMED_BLOCKER_PAUSED = "CODER_SESSION_PAUSED"
CLI_PREFLIGHT_BLOCKERS = frozenset({NAMED_BLOCKER_CLI, NAMED_BLOCKER_CLI_CREDS})
NO_MODEL_CONFIGURED_HINT = "No model configured"

#: #324 wrote the CLI 0.41 docs example ``kimi-code/k3`` (API id ``k3``).
#: Live sess_d70c18ef58ab48e6: Moonshot ``api.moonshot.ai`` answered 404 /
#: Permission denied for that id. This factory's HTTP coder already uses
#: ``kimi-k2.7-code`` on the same endpoint (``llm_config._kimi_model``).
KIMI_CODE_MODEL_ENV = "KIMI_CODE_MODEL"
KIMI_CODE_MODEL_ID_ENV = "KIMI_CODE_MODEL_ID"
DEFAULT_KIMI_CODE_MODEL = "kimi-k2.7-code"
LEGACY_K3_ALIASES = frozenset({"kimi-code/k3", "k3"})
_MODEL_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_DEFAULT_MODEL_RE = re.compile(
    r'(?m)^\s*default_model\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))'
)

#: One-line operator note. Dashboard clicks stay owner-gated.
OWNER_GATED_CLI_LOG = (
    "FACTORY_CODE_CLI / KIMI_CODE_API_KEY owner-gated on Render — not claimed set"
)


class CodeCliUnavailable(RuntimeError):
    """Operator/config class: FACTORY_CODE_CLI is not on the factory host."""

    blocker = NAMED_BLOCKER_CLI


class CodeCliCredentialsMissing(CodeCliUnavailable):
    """Kimi CLI is on PATH but ~/.kimi-code/config.toml is missing."""

    blocker = NAMED_BLOCKER_CLI_CREDS


class CodeCliFailed(RuntimeError):
    """CLI ran and exited non-zero. Not a silent template success."""

    blocker = NAMED_BLOCKER_CLI_FAILED


class CodeCliNoModelConfigured(CodeCliFailed):
    """CLI refused: no default_model (headless /login is not available)."""

    blocker = NAMED_BLOCKER_CLI_NO_MODEL


class CodeCliModelDenied(CodeCliFailed):
    """CLI reached the API; the key cannot call the configured model id."""

    blocker = NAMED_BLOCKER_CLI_MODEL_DENIED


def brief_dispatch_enabled() -> bool:
    """Default ON. FACTORY_BRIEF_DISPATCH=0 restores per-capability shots."""
    raw = os.getenv(BRIEF_DISPATCH_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def http_oneshot_enabled() -> bool:
    """CI/dev escape only. Production Floor must not set this."""
    raw = os.getenv(BRIEF_HTTP_ONESHOT_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def brief_requires_cli() -> bool:
    """Keyed brief path must dispatch via FACTORY_CODE_CLI.

    Unkeyed / ``FACTORY_CODER_ENABLED=0`` still uses honest templates.
    ``FACTORY_BRIEF_HTTP_ONESHOT=1`` keeps the CI oneshot contract.
    ``ENV=test`` does not refuse at generate-start unless
    ``FACTORY_BRIEF_REQUIRE_CLI=1`` (the mutation). Production
    (``ENV=production``) and keyed non-test hosts fail-closed.
    """
    if not brief_dispatch_enabled():
        return False
    if http_oneshot_enabled():
        return False
    from app.factory.coder import coder_enabled

    if not coder_enabled():
        return False
    require = os.getenv("FACTORY_BRIEF_REQUIRE_CLI", "").strip().lower()
    if require in {"0", "false", "no", "off"}:
        return False
    if require in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("ENV", "").strip().lower() != "test"


def cli_unavailable_detail(command: Optional[str] = None) -> str:
    """Named-class operator text. Names the env, not a dashboard click."""
    from app.factory.coder import CODE_CLI_ENV, LEGACY_CODE_CLI_ENV, code_cli_command

    cli = (command or code_cli_command()).strip() or "kimi"
    return (
        f"{NAMED_BLOCKER_CLI}: {cli!r} is not an executable on this host. "
        f"Set {CODE_CLI_ENV} (wins) or {LEGACY_CODE_CLI_ENV} to the agentic "
        "coder binary (`kimi` or `claude`, or an absolute path) and provide "
        "CLI credentials (KIMI_CODE_API_KEY writes ~/.kimi-code/config.toml; "
        "Claude uses its own login). HTTP oneshot is not a FACTORY_CODE_CLI "
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
    """
    from app.factory.coder import code_cli_command

    cli = (command or code_cli_command()).strip()
    names = [Path(cli).name.lower()] if cli else []
    resolved = resolve_code_cli(cli) if cli else resolve_code_cli()
    if resolved:
        names.append(Path(resolved).name.lower())
    return any("kimi" in name for name in names)


def cli_credentials_ok(command: Optional[str] = None) -> bool:
    if not cli_requires_kimi_credentials(command):
        return True
    return credentials_file_present()


def cli_credentials_missing_detail(command: Optional[str] = None) -> str:
    """Named-class operator text for binary-present / credentials-absent."""
    from app.factory.coder import CODE_CLI_ENV, code_cli_command

    cli = (command or code_cli_command()).strip() or "kimi"
    dest = kimi_credentials_file()
    return (
        f"{NAMED_BLOCKER_CLI_CREDS}: {cli!r} is an executable on this host but "
        f"{dest} is missing (credentials_file_present=false). "
        "Set KIMI_CODE_API_KEY so boot writes [providers.kimi] and "
        "default_model (KIMI_CODE_MODEL, default kimi-k2.7-code) into that file "
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
    from app.factory.coder import code_cli_command

    command = code_cli_command()
    resolved = resolve_code_cli(command)
    creds_ok = credentials_file_present()
    probe: Dict[str, Any] = {
        "command": command,
        "available": bool(resolved),
        "resolved": resolved,
        "credentials_file_present": creds_ok,
        "requires_cli": brief_requires_cli(),
        "requires_kimi_credentials": cli_requires_kimi_credentials(command),
    }
    if not resolved:
        probe["blocker"] = NAMED_BLOCKER_CLI
        probe["error"] = cli_unavailable_detail(command)
    elif brief_requires_cli() and not cli_credentials_ok(command):
        probe["blocker"] = NAMED_BLOCKER_CLI_CREDS
        probe["error"] = cli_credentials_missing_detail(command)
    return probe


def kimi_code_default_model() -> str:
    """Alias written as ``default_model``. ``KIMI_CODE_MODEL`` overrides."""
    raw = os.getenv(KIMI_CODE_MODEL_ENV, "").strip()
    if raw and _MODEL_ALIAS_RE.match(raw):
        return raw
    return DEFAULT_KIMI_CODE_MODEL


def operator_set_kimi_code_model() -> bool:
    raw = os.getenv(KIMI_CODE_MODEL_ENV, "").strip()
    return bool(raw and _MODEL_ALIAS_RE.match(raw))


def is_legacy_k3_alias(alias: str) -> bool:
    """#324 docs-example id that Moonshot 404s / Permission-denied."""
    return str(alias or "").strip() in LEGACY_K3_ALIASES


def kimi_code_model_id(alias: Optional[str] = None) -> str:
    """API model id for the ``[models]`` table (last path segment of the alias)."""
    override = os.getenv(KIMI_CODE_MODEL_ID_ENV, "").strip()
    if override and _MODEL_ALIAS_RE.match(override):
        return override
    name = alias or kimi_code_default_model()
    return name.rsplit("/", 1)[-1]


def config_default_model(text: str) -> str:
    match = _DEFAULT_MODEL_RE.search(text or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or match.group(3) or "").strip()


def config_has_kimi_provider(text: str) -> bool:
    return "[providers.kimi]" in (text or "")


def config_has_model_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    blob = text or ""
    if f'[models."{alias}"]' in blob:
        return True
    if "/" not in alias and "." not in alias:
        return f"[models.{alias}]" in blob
    return False


def _model_context_size(model_id: str) -> int:
    # k3 window from Kimi Code CLI 0.41 config-files example; others 256k.
    return 1048576 if model_id == "k3" else 262144


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


def apply_kimi_code_default_model(
    text: str, *, alias: Optional[str] = None
) -> Tuple[str, bool]:
    """Insert ``default_model`` + ``[models]`` when missing. Does not strip keys.

    Replaces the #324 ``kimi-code/k3`` / ``k3`` default when the operator
    has not set ``KIMI_CODE_MODEL`` — that id 404s on api.moonshot.ai.
    """
    alias = alias or kimi_code_default_model()
    model_id = kimi_code_model_id(alias)
    out = text or ""
    current = config_default_model(out)
    mutated = False
    if current and is_legacy_k3_alias(current) and not operator_set_kimi_code_model():
        out = _DEFAULT_MODEL_RE.sub(f'default_model = "{alias}"', out, count=1)
        current = alias
        mutated = True
    if not current:
        out = re.sub(r"(?m)^\s*default_model\s*=\s*(?:\"\"|''|\s*)\s*\n?", "", out)
        prefix = f'default_model = "{alias}"\n'
        body = out.lstrip("\n")
        out = prefix + ("\n" + body if body else "")
        current = alias
        mutated = True
    if not config_has_model_alias(out, current):
        mid = model_id if current == alias else current.rsplit("/", 1)[-1]
        out = _ensure_trailing_newline(out)
        if out and not out.endswith("\n\n"):
            out += "\n"
        out += _model_table_block(current, mid)
        mutated = True
    return out, mutated


def _looks_like_model_denied(output: str) -> bool:
    """Live #324 k3: Moonshot 404 / Permission denied for the configured id."""
    blob = output or ""
    low = blob.lower()
    if "permission denied" in low:
        return True
    if "invalid model" in low or "unknown model" in low or "model_not_found" in low:
        return True
    if "404" in blob and any(
        tok in low for tok in ("model", "k3", "kimi-code", "not found")
    ):
        return True
    return False


def classify_cli_exit(code: int, output: str) -> Tuple[str, str]:
    """Named fail-closed class for a non-zero FACTORY_CODE_CLI exit.

    ``FACTORY_CODE_CLI_FAILED`` stays the generic honesty class. A more
    specific ``FACTORY_CODE_CLI_NO_MODEL`` fires when the CLI prints
    ``No model configured`` (headless /login is not a Floor path).
    ``FACTORY_CODE_CLI_MODEL_DENIED`` fires when the key cannot call the
    configured id (live k3 / kimi-code/k3 → 404 or Permission denied).
    A templated pilot zip is not a ≥2h CLI session.
    """
    exit_bit = f"CLI exited {code}"
    if NO_MODEL_CONFIGURED_HINT in (output or ""):
        return (
            NAMED_BLOCKER_CLI_NO_MODEL,
            (
                f"{exit_bit} — No model configured. Headless Floor cannot run "
                "`kimi` /login. Set KIMI_CODE_MODEL (default "
                f"{DEFAULT_KIMI_CODE_MODEL} on api.moonshot.ai; not the CLI "
                "docs example kimi-code/k3) so boot writes default_model "
                "into ~/.kimi-code/config.toml. A templated "
                f"pilot zip is not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
            ),
        )
    if _looks_like_model_denied(output):
        return (
            NAMED_BLOCKER_CLI_MODEL_DENIED,
            (
                f"{exit_bit} — configured model denied (404 / Permission "
                "denied). #324 default kimi-code/k3 (API id k3) is not on "
                "this Moonshot key. Set KIMI_CODE_MODEL to a catalog id this "
                f"key can call (boot default {DEFAULT_KIMI_CODE_MODEL}, same "
                "as CEREBRUM_FACTORY_LLM_MODEL). Headless Floor cannot run "
                f"`kimi` /login. A templated pilot zip is not a ≥2h CLI "
                f"session. {OWNER_GATED_CLI_LOG}."
            ),
        )
    return NAMED_BLOCKER_CLI_FAILED, exit_bit


def ensure_code_cli_credentials() -> Dict[str, Any]:
    """Write ~/.kimi-code/config.toml from KIMI_CODE_API_KEY when present.

    Also writes ``default_model`` (``KIMI_CODE_MODEL``, default
    ``kimi-k2.7-code`` — this factory's Moonshot HTTP coder id). The
    CLI 0.41 docs example ``kimi-code/k3`` 404s on api.moonshot.ai
    (sess_d70c18ef58ab48e6). Mutates an existing credentials-only file
    and migrates a leftover #324 k3 default when ``KIMI_CODE_MODEL`` is
    unset. Does not install the binary. Skipped when the secret is
    unset — owner-gated stays owner-gated. Does not claim the Render
    dashboard is set.
    """
    key = (
        os.getenv("KIMI_CODE_API_KEY", "").strip()
        or os.getenv("KIMI_CODE_KEY", "").strip()
    )
    home = kimi_credentials_home()
    dest = kimi_credentials_file()
    alias = kimi_code_default_model()
    if not key:
        logger.info(OWNER_GATED_CLI_LOG)
        return {
            "ok": False,
            "wrote": False,
            "mutated": False,
            "reason": "KIMI_CODE_API_KEY unset",
        }
    existed = dest.is_file()
    existing = dest.read_text(encoding="utf-8") if existed else ""
    has_provider = config_has_kimi_provider(existing)
    current_model = config_default_model(existing)
    migrate_k3 = is_legacy_k3_alias(current_model) and not operator_set_kimi_code_model()
    if (
        has_provider
        and current_model
        and config_has_model_alias(existing, current_model)
        and not migrate_k3
    ):
        return {
            "ok": True,
            "wrote": False,
            "mutated": False,
            "path": str(dest),
            "model": current_model,
            "reason": "already present",
        }
    home.mkdir(parents=True, exist_ok=True)
    base_url = os.getenv("KIMI_CODE_BASE_URL", "https://api.moonshot.ai/v1").strip()
    text = existing
    wrote_provider = False
    if not has_provider:
        text = _ensure_trailing_newline(text)
        text = text + ("\n" if text else "") + _provider_block(key, base_url)
        wrote_provider = True
    text, mutated_model = apply_kimi_code_default_model(text, alias=alias)
    dest.write_text(text, encoding="utf-8")
    logger.info(
        "wrote Kimi Code CLI credentials to %s (model=%s; binary still required)",
        dest,
        config_default_model(text) or alias,
    )
    return {
        "ok": True,
        "wrote": wrote_provider,
        "mutated": bool(existed and mutated_model),
        "path": str(dest),
        "model": config_default_model(text) or alias,
        "reason": "mutated" if existed and mutated_model else "wrote",
    }


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
    receipt: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "via": self.via,
            "ok": self.ok,
            "detail": self.detail,
            "model": self.model,
            "blocker": self.blocker,
            "handler_ids": sorted(self.handlers),
            "kept_handler_ids": sorted(self.kept_handler_ids),
            "spec_ids": sorted(self.specs),
            "receipt": dict(self.receipt),
        }


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
    from app.factory.coder import code_cli_command

    root = _workspace_root(ctx)
    log_path = root / LOG_REL
    cli = resolve_code_cli() or code_cli_command()
    brief_arg = f"@{BRIEF_REL.as_posix()}"
    cmd = [cli, "--prompt", brief_arg, "--add-dir", "."]
    _append_log(log_path, f"$ {' '.join(cmd)}")
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
    that is how PRODUCT ``workflow: step_N (event_bus): error`` locked
    in after #318 (sess_a4690fb3336c42fb).
    """
    blob = text or ""
    if "def handle(" not in blob:
        return False
    if not handler_satisfies_event_bus_contract(blob):
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
    """Hand the compiled brief to the agentic coder. One session."""
    root = _workspace_root(ctx)
    timeout_s = 1500.0
    left = ctx.coder_time_left() if hasattr(ctx, "coder_time_left") else None
    if left is not None:
        timeout_s = max(30.0, float(left) - 15.0)

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
    elif cli_available():
        result = _run_cli_session(ctx, compiled, timeout_s=timeout_s)
        if result.ok:
            # #318 keep-path: prefer on-disk workflow/event_bus steps over a
            # thin JSON body so the fallback envelope cannot overwrite them.
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
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

    receipt = {
        "via": result.via,
        "ok": result.ok,
        "detail": result.detail,
        "blocker": result.blocker,
        "model": result.model,
        "product_id": compiled.product_id,
        "vertical": compiled.vertical,
        "capabilities": list(compiled.capabilities),
        "inventory_reuse": [
            item.capability_id
            for item in compiled.inventory
            if item.verified_present and not item.missing
        ],
        "inventory_gaps": [
            item.capability_id for item in compiled.inventory if item.is_gap
        ],
        "harvested_spec_ids": sorted(result.specs),
        "kept_handler_ids": list(result.kept_handler_ids),
    }
    result.receipt = receipt
    try:
        ctx.workspace.write_text(
            RECEIPT_REL,
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        )
    except Exception:  # noqa: BLE001 — receipt must not fail the role
        _write_receipt(root, receipt)
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
