"""One-session coder dispatch + owner Pause/Stop control.

The WRITER compiles one brief, then this module hands it to FACTORY_CODE_CLI
(or a single HTTP oneshot when the CLI is not on PATH). Per-capability
handle() shots are not this path.

Control is a file the Floor writes. The dispatcher polls it: pause waits,
stop terminates the session. Owner eyes are the monitor.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger("cerebrumdev.factory.coder_session")

BRIEF_REL = Path("docs") / "coder_brief.md"
LOG_REL = Path("docs") / "coder_session.log"
CONTROL_REL = Path("docs") / "coder_control.json"
RECEIPT_REL = Path("docs") / "coder_receipt.json"

CONTROL_RUN = "run"
CONTROL_PAUSE = "pause"
CONTROL_STOP = "stop"

BRIEF_DISPATCH_ENV = "FACTORY_BRIEF_DISPATCH"
NAMED_BLOCKER_CLI = "FACTORY_CODE_CLI_UNAVAILABLE"
NAMED_BLOCKER_STOPPED = "CODER_SESSION_STOPPED"
NAMED_BLOCKER_PAUSED = "CODER_SESSION_PAUSED"


def brief_dispatch_enabled() -> bool:
    """Default ON. FACTORY_BRIEF_DISPATCH=0 restores per-capability shots."""
    raw = os.getenv(BRIEF_DISPATCH_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


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


def cli_available(command: Optional[str] = None) -> bool:
    from app.factory.coder import code_cli_command

    cli = (command or code_cli_command()).strip()
    if not cli:
        return False
    path = Path(cli)
    if path.is_file() and os.access(path, os.X_OK):
        return True
    return shutil.which(cli) is not None


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
    cli = code_cli_command()
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
        return DispatchResult(
            via="cli",
            ok=False,
            detail=f"CLI exited {code}",
            blocker="FACTORY_CODE_CLI_FAILED",
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

    Brief-driven workflow steps must survive the fallback envelope even
    when the module omitted CAPABILITY_ID (FACTORY_CODE_CLI often writes
    handle() + execute("workflow") without the factory header).
    """
    blob = text or ""
    if "def handle(" not in blob:
        return False
    if "CAPABILITY_ID" in blob:
        return True
    return _has_brief_workflow_steps(blob)


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
    # Prefer a keepable on-disk workflow handler over an oneshot body that
    # never named event_bus steps — wrapping that body is the envelope
    # rewrite that dropped brief-driven steps (sess_e94ddfa797ea4a45).
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
        if _has_brief_workflow_steps(disk) and not _has_brief_workflow_steps(body):
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

    if cli_available():
        result = _run_cli_session(ctx, compiled, timeout_s=timeout_s)
        if result.ok:
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
    else:
        _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI}] falling back to HTTP oneshot or templates")
        result = _http_oneshot(ctx, compiled)
        if result.via == "skipped":
            result.blocker = NAMED_BLOCKER_CLI
        # Oneshot / template fallback still harvests workspace files so
        # brief-driven workflow steps survive the envelope rewrite.
        _merge_workspace_harvest(result, root, list(compiled.capabilities))

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
