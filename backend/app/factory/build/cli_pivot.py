"""N1 seam — compose C-BRIEF → Cursor Background Agent → N1b → N3 later.

Deterministic fill from blueprint + registry + kit. No LLM writes brief text.
No handler-body writes inside Factory.

When Cursor executor keys are absent, launch fail-closes as
``EXECUTOR_UNAVAILABLE`` (infra). When keys **and**
``CEREBRUM_BUILDS_GITHUB_TOKEN`` are present, N1a pushes the workspace to
a ``build/**`` scratch branch on private cerebrum-builds and launches a
Cursor Background Agent against that branch. Infra misses (API down,
never started, hung past wall, push failed) stay
``EXECUTOR_UNAVAILABLE``. Content misses stay N1b.

The NEW path must not fall back to on-box kimi / FACTORY_CODE_CLI
authorship or ``_templated_body``. The old path may remain until N2;
this module does not call it.

Budget (wall + spend) is decided **before** dispatch. Breach ledgers
``BUDGET_EXCEEDED``. This seam never calls ``_extend_wall`` (S07).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import CompiledBrief, compile_brief
from app.factory.build.budget_inspect import CEILING_S
from app.factory.build.builds_push import BuildsPushError
from app.factory.build.cli_receipt import (
    HANDOFF_TO_N3,
    PATHS_VIOLATED,
    RECEIPT_INVALID,
    ReceiptInvalid,
    PathsViolated,
    blueprint_capability_set,
    enforce_receipt,
)
from app.factory.build.cursor_ba import CursorBAError, run_background_agent
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.product_architect import plan_blueprint

EXECUTOR_UNAVAILABLE = "EXECUTOR_UNAVAILABLE"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

#: Infra vs content — never conflate these (run9).
CLASS_INFRA = "infra"
CLASS_CONTENT = "content"

CURSOR_KEY_ENVS = (
    "CURSOR_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "FACTORY_CURSOR_API_KEY",
)

DEFAULT_WALL_S = 1800.0
DEFAULT_SPEND_USD = 0.0

BRIEF_REL = Path("docs") / "coder_brief.md"
LEDGER_REL = Path("build_ledger.jsonl")

# Do not call these from this module (N1 new path / no author fallback):
# _templated_body  dispatch_compiled_brief  generate_from_compiled_brief


class ExecutorUnavailable(RuntimeError):
    """Cursor API down / agent never started / hung past wall (infra)."""

    honesty = EXECUTOR_UNAVAILABLE
    failure_class = CLASS_INFRA


class BudgetExceeded(RuntimeError):
    """Wall or spend cap decided before dispatch was breached."""

    honesty = BUDGET_EXCEEDED
    failure_class = CLASS_INFRA


@dataclass(frozen=True)
class PivotBudget:
    """Caps frozen before the executor is launched. Never extended."""

    wall_s: float
    spend_cap_usd: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wall_s": self.wall_s,
            "spend_cap_usd": self.spend_cap_usd,
            "extend_wall": False,
            "decided_before_dispatch": True,
        }


def decide_budget(
    *,
    wall_s: Optional[float] = None,
    spend_cap_usd: Optional[float] = None,
) -> PivotBudget:
    """Choose wall + spend **before** dispatch. Clamp to S07; never extend."""
    if wall_s is None:
        raw = os.getenv("FACTORY_CLI_PIVOT_WALL_S", "").strip()
        wall_s = float(raw) if raw else DEFAULT_WALL_S
    if spend_cap_usd is None:
        raw = os.getenv("FACTORY_CLI_PIVOT_SPEND_USD", "").strip()
        spend_cap_usd = float(raw) if raw else DEFAULT_SPEND_USD
    wall = float(wall_s)
    spend = float(spend_cap_usd)
    if wall > CEILING_S:
        wall = float(CEILING_S)
    if wall < 0:
        wall = 0.0
    if spend < 0:
        spend = 0.0
    return PivotBudget(wall_s=wall, spend_cap_usd=spend)


def check_budget(
    budget: PivotBudget,
    *,
    elapsed_s: float = 0.0,
    spent_usd: float = 0.0,
) -> None:
    if elapsed_s > budget.wall_s or spent_usd > budget.spend_cap_usd:
        raise BudgetExceeded(
            f"{BUDGET_EXCEEDED}: elapsed_s={elapsed_s} wall_s={budget.wall_s} "
            f"spent_usd={spent_usd} spend_cap_usd={budget.spend_cap_usd}"
        )


def cursor_keys_present(env: Optional[Mapping[str, str]] = None) -> bool:
    blob = env if env is not None else os.environ
    return any(str(blob.get(name) or "").strip() for name in CURSOR_KEY_ENVS)


def writer_uses_cli_pivot(env: Optional[Mapping[str, str]] = None) -> bool:
    """Generate/Continue takes this seam when Cursor executor keys exist.

    Keys-present is the gate (no extra ``FACTORY_CLI_PIVOT`` flag). Missing
    keys keep today's in-process WRITER until N2. An extra env that
    operators forget would leave live Generate on the old path; an extra
    env without keys would skip WRITER into ``EXECUTOR_UNAVAILABLE``.
    """
    return cursor_keys_present(env)


def resolve_pivot_session_env(
    workspace: Path,
    env: Optional[Mapping[str, str]] = None,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, str]:
    """Copy env and pin ``FACTORY_SESSION_ID`` so ``build/<session>-*`` is stable."""
    blob = dict(os.environ if env is None else env)
    if any(str(blob.get(name) or "").strip() for name in (
        "FACTORY_CLI_PIVOT_SESSION_ID",
        "FACTORY_SESSION_ID",
    )):
        return blob
    sid = str(session_id or "").strip()
    if not sid:
        from app.factory.build.orphan_recovery import session_id_from_output

        sid = session_id_from_output(workspace) or ""
    if sid:
        blob["FACTORY_SESSION_ID"] = sid
    return blob


def run_writer_via_cli_pivot(
    ctx: Any,
    *,
    launch: Optional[Callable[..., ExecutorLaunch]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Any:
    """Skip in-process WRITER authorship; run ``run_cli_pivot`` on the clone."""
    from app.factory.build.roles_models import RoleError, RoleResult

    dest = getattr(getattr(ctx, "workspace", None), "destination", None)
    if dest is None:
        dest = Path(getattr(ctx.workspace, "workspace", ctx.workspace))
    dest = Path(dest)
    state = getattr(ctx, "state", None)
    if not isinstance(state, dict):
        state = {}
    if launch is None:
        launch = state.get("cli_pivot_launch")
    env_map = resolve_pivot_session_env(
        dest,
        env,
        session_id=str(state.get("session_id") or ""),
    )
    ledger_path = dest / LEDGER_REL
    ledger = BuildLedger(ledger_path) if ledger_path.is_file() else None
    seam = run_cli_pivot(
        ctx.blueprint,
        dest,
        plan=getattr(ctx, "plan", None),
        blocks_root=getattr(ctx, "blocks_root", None),
        ledger=ledger,
        env=env_map,
        launch=launch,
    )
    payload = seam.to_dict()
    state["cli_pivot"] = payload
    note = getattr(ctx, "note", None)
    if callable(note):
        note(
            f"cli-pivot {seam.honesty}: {seam.detail}",
            stage="cli_pivot",
            honesty=seam.honesty,
            next=seam.next,
            green=False,
        )
    if seam.honesty == HANDOFF_TO_N3:
        return RoleResult(
            ok=True,
            detail=seam.detail,
            notes={
                "cli_pivot": payload,
                "next": seam.next or "n3_gate",
            },
        )
    raise RoleError(seam.detail)


def compose_cbrief(
    blueprint: Any,
    *,
    plan: Any = None,
    blocks_root: Optional[Path] = None,
    store_ids: Optional[Sequence[str]] = None,
    budget_s: Optional[float] = None,
) -> CompiledBrief:
    """Deterministic C-BRIEF. LLM never writes this text."""
    resolved_plan = plan if plan is not None else plan_blueprint(
        blueprint, blocks_root=blocks_root
    )
    return compile_brief(
        blueprint,
        resolved_plan,
        blocks_root=blocks_root,
        store_ids=store_ids,
        budget_s=budget_s,
    )


@dataclass(frozen=True)
class ExecutorLaunch:
    """What the live Cursor Background Agent (or an injected stub) returns."""

    started: bool
    hung: bool = False
    elapsed_s: float = 0.0
    spent_usd: float = 0.0
    receipt: Any = None
    changed_paths: List[str] = field(default_factory=list)
    unified_diff: str = ""
    branch: str = ""
    head_sha: str = ""
    owner: str = ""
    repo: str = ""


def _session_id(
    brief: CompiledBrief,
    workspace: Path,
    env: Mapping[str, str],
) -> str:
    for name in ("FACTORY_CLI_PIVOT_SESSION_ID", "FACTORY_SESSION_ID"):
        value = str(env.get(name) or "").strip()
        if value:
            return value
    product = str(getattr(brief, "product_id", "") or "").strip()
    if product:
        return product
    return Path(workspace).name or "session"


def launch_executor(
    *,
    brief: CompiledBrief,
    budget: PivotBudget,
    workspace: Path,
    env: Optional[Mapping[str, str]] = None,
    launch: Optional[Callable[..., ExecutorLaunch]] = None,
) -> ExecutorLaunch:
    """Fail-closed infra launch. No kimi / FACTORY_CODE_CLI / template fallback."""
    if launch is not None:
        return launch(budget=budget)
    env_map: Mapping[str, str] = env if env is not None else os.environ
    if not cursor_keys_present(env_map):
        raise ExecutorUnavailable(
            f"{EXECUTOR_UNAVAILABLE}: Cursor executor keys absent "
            f"({', '.join(CURSOR_KEY_ENVS)}) — fail-closed; no FACTORY_CODE_CLI "
            "/ kimi / template-body fallback"
        )
    try:
        result = run_background_agent(
            workspace=Path(workspace),
            wall_s=budget.wall_s,
            env=env_map,
            session_id=_session_id(brief, Path(workspace), env_map),
        )
    except (BuildsPushError, CursorBAError) as exc:
        raise ExecutorUnavailable(f"{EXECUTOR_UNAVAILABLE}: {exc}") from exc
    return ExecutorLaunch(
        started=result.started,
        hung=result.hung,
        elapsed_s=result.elapsed_s,
        spent_usd=result.spent_usd,
        receipt=result.receipt,
        changed_paths=list(result.changed_paths),
        unified_diff=result.unified_diff,
        branch=getattr(result, "branch", "") or "",
        head_sha=getattr(result, "head_sha", "") or "",
        owner=getattr(result, "owner", "") or "",
        repo=getattr(result, "repo", "") or "",
    )


@dataclass
class SeamResult:
    honesty: str
    failure_class: Optional[str]
    green: bool
    next: Optional[str]
    detail: str
    budget: Dict[str, Any] = field(default_factory=dict)
    cli_authored_ids: List[str] = field(default_factory=list)
    capability_set: List[str] = field(default_factory=list)
    brief_chars: int = 0
    builds_branch: str = ""
    builds_sha: str = ""
    builds_owner: str = ""
    builds_repo: str = ""

    @property
    def ok(self) -> bool:
        return self.honesty == HANDOFF_TO_N3

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "honesty": self.honesty,
            "failure_class": self.failure_class,
            "green": False,
            "next": self.next,
            "detail": self.detail,
            "ok": self.ok,
            "budget": dict(self.budget),
            "cli_authored_ids": list(self.cli_authored_ids),
            "capability_set": list(self.capability_set),
            "brief_chars": self.brief_chars,
        }
        if self.builds_branch:
            payload["builds_branch"] = self.builds_branch
        if self.builds_sha:
            payload["builds_sha"] = self.builds_sha
        if self.builds_owner:
            payload["builds_owner"] = self.builds_owner
        if self.builds_repo:
            payload["builds_repo"] = self.builds_repo
        return payload


def _ledger_note(
    ledger: Optional[BuildLedger],
    honesty: str,
    detail: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> None:
    if ledger is None:
        return
    body = dict(payload or {})
    body["honesty"] = honesty
    body["seam"] = "cli_pivot"
    kind = EventKind.GATE_FAILED if honesty != HANDOFF_TO_N3 else EventKind.NOTE
    ledger.append(
        kind,
        role=BuildRole.WRITER,
        detail=detail,
        payload=body,
    )


def run_cli_pivot(
    blueprint: Any,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
    store_ids: Optional[Sequence[str]] = None,
    plan: Any = None,
    wall_s: Optional[float] = None,
    spend_cap_usd: Optional[float] = None,
    ledger: Optional[BuildLedger] = None,
    env: Optional[Mapping[str, str]] = None,
    launch: Optional[Callable[..., ExecutorLaunch]] = None,
    elapsed_s: float = 0.0,
    spent_usd: float = 0.0,
    write_brief: bool = True,
) -> SeamResult:
    """Compose → freeze budget → launch → N1b. Never green from this function."""
    workspace = Path(output_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    led = ledger or BuildLedger(workspace / LEDGER_REL)
    if not led.exists():
        led.start_run(
            product_id=str(getattr(blueprint, "product_id", "") or "product"),
            inputs_hash="cli_pivot",
        )

    budget = decide_budget(wall_s=wall_s, spend_cap_usd=spend_cap_usd)
    caps = sorted(blueprint_capability_set(blueprint))
    try:
        check_budget(budget, elapsed_s=elapsed_s, spent_usd=spent_usd)
    except BudgetExceeded as exc:
        _ledger_note(
            led,
            BUDGET_EXCEEDED,
            str(exc),
            {"class": CLASS_INFRA, "budget": budget.to_dict()},
        )
        return SeamResult(
            honesty=BUDGET_EXCEEDED,
            failure_class=CLASS_INFRA,
            green=False,
            next=None,
            detail=str(exc),
            budget=budget.to_dict(),
            capability_set=caps,
        )

    compiled = compose_cbrief(
        blueprint,
        plan=plan,
        blocks_root=blocks_root,
        store_ids=store_ids,
        budget_s=budget.wall_s,
    )
    if write_brief:
        dest = workspace / BRIEF_REL
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(compiled.text, encoding="utf-8")

    try:
        outcome = launch_executor(
            brief=compiled,
            budget=budget,
            workspace=workspace,
            env=env,
            launch=launch,
        )
    except ExecutorUnavailable as exc:
        _ledger_note(
            led,
            EXECUTOR_UNAVAILABLE,
            str(exc),
            {"class": CLASS_INFRA},
        )
        return SeamResult(
            honesty=EXECUTOR_UNAVAILABLE,
            failure_class=CLASS_INFRA,
            green=False,
            next=None,
            detail=str(exc),
            budget=budget.to_dict(),
            capability_set=caps,
            brief_chars=len(compiled.text),
        )

    if outcome.hung or not outcome.started:
        detail = (
            f"{EXECUTOR_UNAVAILABLE}: agent hung past wall"
            if outcome.hung
            else f"{EXECUTOR_UNAVAILABLE}: agent never started"
        )
        _ledger_note(led, EXECUTOR_UNAVAILABLE, detail, {"class": CLASS_INFRA})
        return SeamResult(
            honesty=EXECUTOR_UNAVAILABLE,
            failure_class=CLASS_INFRA,
            green=False,
            next=None,
            detail=detail,
            budget=budget.to_dict(),
            capability_set=caps,
            brief_chars=len(compiled.text),
        )

    try:
        check_budget(
            budget,
            elapsed_s=outcome.elapsed_s,
            spent_usd=outcome.spent_usd,
        )
    except BudgetExceeded as exc:
        _ledger_note(
            led,
            BUDGET_EXCEEDED,
            str(exc),
            {"class": CLASS_INFRA, "budget": budget.to_dict()},
        )
        return SeamResult(
            honesty=BUDGET_EXCEEDED,
            failure_class=CLASS_INFRA,
            green=False,
            next=None,
            detail=str(exc),
            budget=budget.to_dict(),
            capability_set=caps,
            brief_chars=len(compiled.text),
        )

    try:
        verdict = enforce_receipt(
            blueprint=blueprint,
            receipt=outcome.receipt,
            workspace=workspace,
            changed_paths=outcome.changed_paths,
            unified_diff=outcome.unified_diff,
        )
    except ReceiptInvalid as exc:
        _ledger_note(led, RECEIPT_INVALID, str(exc), {"class": CLASS_CONTENT})
        return SeamResult(
            honesty=RECEIPT_INVALID,
            failure_class=CLASS_CONTENT,
            green=False,
            next=None,
            detail=str(exc),
            budget=budget.to_dict(),
            capability_set=caps,
            brief_chars=len(compiled.text),
        )
    except PathsViolated as exc:
        _ledger_note(led, PATHS_VIOLATED, str(exc), {"class": CLASS_CONTENT})
        return SeamResult(
            honesty=PATHS_VIOLATED,
            failure_class=CLASS_CONTENT,
            green=False,
            next=None,
            detail=str(exc),
            budget=budget.to_dict(),
            capability_set=caps,
            brief_chars=len(compiled.text),
        )

    builds_payload = {
        "builds_branch": getattr(outcome, "branch", "") or "",
        "builds_sha": getattr(outcome, "head_sha", "") or "",
        "builds_owner": getattr(outcome, "owner", "") or "",
        "builds_repo": getattr(outcome, "repo", "") or "",
    }
    _ledger_note(
        led,
        HANDOFF_TO_N3,
        verdict.detail,
        {
            "class": CLASS_CONTENT,
            "next": "n3_gate",
            "green": False,
            "cli_authored_ids": list(verdict.cli_authored_ids),
            **{k: v for k, v in builds_payload.items() if v},
        },
    )
    return SeamResult(
        honesty=HANDOFF_TO_N3,
        failure_class=None,
        green=False,
        next="n3_gate",
        detail=verdict.detail,
        budget=budget.to_dict(),
        cli_authored_ids=list(verdict.cli_authored_ids),
        capability_set=caps,
        brief_chars=len(compiled.text),
        builds_branch=builds_payload["builds_branch"],
        builds_sha=builds_payload["builds_sha"],
        builds_owner=builds_payload["builds_owner"],
        builds_repo=builds_payload["builds_repo"],
    )
