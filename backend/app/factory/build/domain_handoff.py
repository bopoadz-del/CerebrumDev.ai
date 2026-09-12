"""Post-CLONER domain handoff — FinanceOps command → MR.FINANCE.

After COLLECTOR + CLONER on a finance vertical, deliver the frozen command
(C-BRIEF / ``docs/coder_brief.md`` + workspace pointer + session id + Floor
URL) to the domain owner **without** Grok Bot ``SendToAgent``:

1. Open or update a GitHub issue (labels ``domain:finance`` + ``handoff``).
2. Write ``docs/domain_handoff.json`` on the workspace (idempotency marker).
3. Optionally POST JSON to ``DOMAIN_HANDOFF_WEBHOOK_URL`` when set
   (operators set Render env — this module never writes Render).
   When ``DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION`` (or ``_KEY``) is set,
   the POST includes ``Authorization`` (Bearer). Never commit the value.

MR.FINANCE then produces / checks / posts / deploys. Never calls Cursor,
CloudAgent, or SendToAgent APIs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.factory.build.builds_push import (
    BUILDS_TOKEN_ENV,
    BuildsPushError,
    builds_token,
    github_request,
    parse_builds_repo,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.orphan_recovery import session_id_from_output

logger = logging.getLogger("cerebrumdev.factory.domain_handoff")

HANDOFF_REL = Path("docs") / "domain_handoff.json"
BRIEF_REL = Path("docs") / "coder_brief.md"
DOMAIN_HANDOFF_FIRED = "DOMAIN_HANDOFF_FIRED"
DOMAIN_HANDOFF_WEBHOOK_ENV = "DOMAIN_HANDOFF_WEBHOOK_URL"
DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV = "DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION"
DOMAIN_HANDOFF_WEBHOOK_KEY_ENV = "DOMAIN_HANDOFF_WEBHOOK_KEY"
FLOOR_PUBLIC_URL_ENVS = (
    "FACTORY_PUBLIC_URL",
    "FACTORY_FLOOR_URL",
    "RENDER_EXTERNAL_URL",
    "PUBLIC_APP_URL",
)
DEFAULT_FLOOR_BASE = "https://cerebrumdev.ai"
# Prefer cerebrum-builds for domain issues; fall back via CEREBRUM_BUILDS_REPO.
HANDOFF_ISSUE_REPO_ENV = "DOMAIN_HANDOFF_GITHUB_REPO"

FINANCE_VERTICALS = frozenset(
    {
        "finance",
        "finance_ops",
        "finance-ops",
        "financeops",
    }
)
FINANCE_LABELS = ("domain:finance", "handoff")
HANDOFF_ISSUE_TITLE_PREFIX = "[domain-handoff] finance post-CLONER"


@dataclass
class HandoffResult:
    fired: bool
    skipped: bool = False
    reason: str = ""
    domain: str = ""
    issue_url: str = ""
    issue_number: Optional[int] = None
    webhook_posted: bool = False
    already: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fired": self.fired,
            "skipped": self.skipped,
            "reason": self.reason,
            "domain": self.domain,
            "issue_url": self.issue_url,
            "issue_number": self.issue_number,
            "webhook_posted": self.webhook_posted,
            "already": self.already,
            "payload": dict(self.payload),
        }


def _norm(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def is_finance_domain(
    output_dir: Path | str,
    *,
    product_id: Optional[str] = None,
    vertical: Optional[str] = None,
    blueprint: Any = None,
) -> bool:
    """True for finance_ops / finance-ops product or vertical."""
    candidates: List[str] = []
    for value in (product_id, vertical):
        if value:
            n = _norm(str(value))
            candidates.extend((n, n.replace("-", "_"), n.replace("_", "-")))
    if blueprint is not None:
        for attr in ("product_id", "vertical"):
            val = getattr(blueprint, attr, None)
            if val is None and isinstance(blueprint, Mapping):
                val = blueprint.get(attr)
            if val:
                n = _norm(str(val))
                candidates.extend((n, n.replace("-", "_"), n.replace("_", "-")))
    root = Path(output_dir)
    candidates.extend(
        (
            _norm(root.name),
            _norm(root.name).replace("-", "_"),
        )
    )
    for rel in (
        Path("docs") / "blueprint" / "product_blueprint.json",
        Path("docs") / "product_blueprint.json",
        Path("docs") / "intake_blueprint.json",
    ):
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, Mapping):
            continue
        for key in ("product_id", "vertical", "domain"):
            val = data.get(key)
            if val:
                n = _norm(str(val))
                candidates.extend((n, n.replace("-", "_"), n.replace("_", "-")))
    for item in candidates:
        if not item:
            continue
        if item in FINANCE_VERTICALS:
            return True
        if item.replace("-", "_") in FINANCE_VERTICALS:
            return True
    return False


def _floor_base(env: Mapping[str, str]) -> str:
    for name in FLOOR_PUBLIC_URL_ENVS:
        raw = str(env.get(name) or "").strip().rstrip("/")
        if raw:
            return raw
    return DEFAULT_FLOOR_BASE


def _session_id(output_dir: Path, explicit: Optional[str] = None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    return session_id_from_output(output_dir) or output_dir.parent.name or output_dir.name


def _already_fired(output_dir: Path) -> bool:
    marker = output_dir / HANDOFF_REL
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(data, Mapping) and data.get("stage") == "post_cloner":
                return True
        except (OSError, ValueError):
            pass
    ledger_path = output_dir / "build_ledger.jsonl"
    if not ledger_path.is_file():
        return False
    try:
        for event in BuildLedger(ledger_path).events():
            payload = getattr(event, "payload", None) or {}
            if str(payload.get("honesty") or "") == DOMAIN_HANDOFF_FIRED:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def ensure_coder_brief(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
    blocks_root: Optional[Path] = None,
) -> Path:
    """Ensure ``docs/coder_brief.md`` exists (compile if missing)."""
    root = Path(output_dir)
    dest = root / BRIEF_REL
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    if blueprint is None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            "# coder_brief\n\n(brief unavailable at handoff — open Floor session)\n",
            encoding="utf-8",
        )
        return dest
    from app.factory.build.cli_pivot import compose_cbrief

    compiled = compose_cbrief(blueprint, plan=plan, blocks_root=blocks_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(compiled.text, encoding="utf-8")
    return dest


def build_handoff_payload(
    output_dir: Path | str,
    *,
    session_id: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    product_id: Optional[str] = None,
    brief_path: Optional[Path] = None,
    brief_excerpt: str = "",
) -> Dict[str, Any]:
    root = Path(output_dir)
    blob = env if env is not None else os.environ
    sid = _session_id(root, session_id)
    floor = f"{_floor_base(blob)}/floor?session={quote(sid, safe='')}"
    brief = brief_path or (root / BRIEF_REL)
    excerpt = brief_excerpt
    if not excerpt and brief.is_file():
        text = brief.read_text(encoding="utf-8")
        excerpt = text if len(text) <= 12000 else text[:12000] + "\n\n…[truncated]…"
    return {
        "schema_version": "domain_handoff.v1",
        "stage": "post_cloner",
        "session_id": sid,
        "domain": "finance",
        "vertical": "finance_ops",
        "product_id": str(product_id or root.name or "finance-ops"),
        "workspace": str(root),
        "coder_brief_path": BRIEF_REL.as_posix(),
        "floor_url": floor,
        "instruction": (
            "MR.FINANCE: COLLECTOR+CLONER are done. The frozen command is "
            f"`docs/coder_brief.md` for session `{sid}`. "
            "Take over: produce, check, post, and deploy "
            "(GitHub or as the user directs). Do not wait for Grok Bot "
            "SendToAgent."
        ),
        "coder_brief_excerpt": excerpt,
        "notified_via": ["github_issue", "domain_handoff.json"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_local_handoff(output_dir: Path | str, payload: Mapping[str, Any]) -> Path:
    root = Path(output_dir)
    dest = root / HANDOFF_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Keep excerpt out of the on-disk marker to avoid huge files; issue has it.
    body = {k: v for k, v in dict(payload).items() if k != "coder_brief_excerpt"}
    dest.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return dest


def _ledger_note(output_dir: Path, payload: Mapping[str, Any]) -> None:
    ledger = BuildLedger(output_dir / "build_ledger.jsonl")
    if not ledger.exists():
        ledger.start_run(
            product_id=str(payload.get("product_id") or output_dir.name),
            inputs_hash="domain_handoff",
        )
    from app.factory.build.authority import BuildRole

    ledger.append(
        EventKind.NOTE,
        role=BuildRole.CLONER,
        detail=f"domain handoff fired for {payload.get('domain')} (post-CLONER)",
        payload={
            "honesty": DOMAIN_HANDOFF_FIRED,
            "domain_handoff": "fired",
            "stage": "post_cloner",
            "issue_url": payload.get("issue_url") or "",
            "session_id": payload.get("session_id") or "",
            "floor_url": payload.get("floor_url") or "",
        },
    )


def _issue_repo(env: Mapping[str, str]) -> tuple[str, str, str]:
    override = str(env.get(HANDOFF_ISSUE_REPO_ENV) or "").strip()
    if override:
        return parse_builds_repo({**dict(env), "CEREBRUM_BUILDS_REPO": override})
    return parse_builds_repo(env)


def _json_mutate(
    url: str,
    *,
    token: str,
    body: Mapping[str, Any],
    opener: Callable[..., Any],
    method: str = "POST",
) -> tuple[int, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CerebrumFactory-DomainHandoff",
        "Content-Type": "application/json",
    }
    req = Request(
        url,
        data=json.dumps(dict(body)).encode("utf-8"),
        headers=headers,
        method=method.upper(),
    )
    try:
        with opener(req, timeout=30.0) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        status = int(exc.code)
    except URLError as exc:
        raise BuildsPushError(f"GitHub API down: {exc.reason}") from exc
    text = raw.decode("utf-8", errors="replace") if raw else ""
    if not text:
        return status, {}
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def _ensure_labels(
    owner: str,
    repo: str,
    labels: Sequence[str],
    *,
    token: str,
    opener: Callable[..., Any],
) -> None:
    for name in labels:
        status, _body = github_request(
            "GET",
            f"/repos/{owner}/{repo}/labels/{quote(name, safe='')}",
            token=token,
            opener=opener,
        )
        if status < 400:
            continue
        color = "0e8a16" if name == "handoff" else "1d76db"
        _json_mutate(
            f"https://api.github.com/repos/{owner}/{repo}/labels",
            token=token,
            body={
                "name": name,
                "color": color,
                "description": "Factory post-CLONER domain handoff",
            },
            opener=opener,
        )


def _find_existing_issue(
    owner: str,
    repo: str,
    session_id: str,
    *,
    token: str,
    opener: Callable[..., Any],
) -> Optional[Dict[str, Any]]:
    q = (
        f"repo:{owner}/{repo} is:issue "
        f"label:handoff label:domain:finance "
        f"in:title {session_id}"
    )
    status, body = github_request(
        "GET",
        f"/search/issues?q={quote(q)}&per_page=5",
        token=token,
        opener=opener,
    )
    if status >= 400 or not isinstance(body, Mapping):
        return None
    items = body.get("items") or []
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, Mapping) and item.get("number"):
            return dict(item)
    return None


def open_or_update_handoff_issue(
    payload: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    opener: Callable[..., Any] = urlopen,
) -> Dict[str, Any]:
    token = builds_token(env)
    if not token:
        raise BuildsPushError(f"{BUILDS_TOKEN_ENV} missing for domain handoff issue")
    owner, repo, _url = _issue_repo(env)
    sid = str(payload.get("session_id") or "")
    title = f"{HANDOFF_ISSUE_TITLE_PREFIX} {sid}".strip()
    excerpt = str(payload.get("coder_brief_excerpt") or "")
    body = (
        f"## Domain handoff — finance (post-CLONER)\n\n"
        f"- **session_id:** `{sid}`\n"
        f"- **product_id:** `{payload.get('product_id')}`\n"
        f"- **workspace:** `{payload.get('workspace')}`\n"
        f"- **coder_brief:** `{payload.get('coder_brief_path')}`\n"
        f"- **Floor URL:** {payload.get('floor_url')}\n\n"
        f"### Instruction for MR.FINANCE\n\n"
        f"{payload.get('instruction')}\n\n"
        f"### Frozen C-BRIEF (`docs/coder_brief.md`)\n\n"
        f"```markdown\n{excerpt}\n```\n\n"
        f"_Opened by Factory domain_handoff after CLONER (no SendToAgent)._\n"
    )
    try:
        _ensure_labels(owner, repo, FINANCE_LABELS, token=token, opener=opener)
    except Exception:  # noqa: BLE001
        logger.exception("domain handoff: ensure labels failed")

    existing = _find_existing_issue(owner, repo, sid, token=token, opener=opener)
    if existing:
        number = int(existing["number"])
        status, updated = _json_mutate(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{number}",
            token=token,
            body={"body": body, "state": "open", "labels": list(FINANCE_LABELS)},
            opener=opener,
            method="PATCH",
        )
        if status >= 400:
            raise BuildsPushError(f"GitHub issue update HTTP {status}")
        return {
            "number": number,
            "html_url": str(
                (updated or {}).get("html_url")
                or existing.get("html_url")
                or f"https://github.com/{owner}/{repo}/issues/{number}"
            ),
            "updated": True,
        }

    status, created = _json_mutate(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        token=token,
        body={"title": title, "body": body, "labels": list(FINANCE_LABELS)},
        opener=opener,
    )
    if status >= 400 or not isinstance(created, Mapping) or not created.get("number"):
        raise BuildsPushError(f"GitHub issue create HTTP {status}: {created!r}")
    return {
        "number": int(created["number"]),
        "html_url": str(created.get("html_url") or ""),
        "updated": False,
    }


def webhook_authorization_header(env: Mapping[str, str]) -> Optional[str]:
    """Return the ``Authorization`` header value, or ``None`` if unset.

    Reads ``DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION``, then
    ``DOMAIN_HANDOFF_WEBHOOK_KEY``. Never logs the secret.

    - ``Authorization: …`` → use the part after the first colon (then Bearer-normalize).
    - Already ``Bearer …`` → use as-is.
    - Raw token (``crsr_…`` or any other) → prefix ``Bearer ``.
    """
    raw = str(env.get(DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV) or "").strip()
    if not raw:
        raw = str(env.get(DOMAIN_HANDOFF_WEBHOOK_KEY_ENV) or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        if not raw:
            return None
    if raw.lower().startswith("bearer "):
        return raw
    return f"Bearer {raw}"


def post_webhook(
    payload: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    opener: Callable[..., Any] = urlopen,
) -> bool:
    url = str(env.get(DOMAIN_HANDOFF_WEBHOOK_ENV) or "").strip()
    if not url:
        return False
    # Do not ship the full brief excerpt to arbitrary webhooks by default —
    # include paths + instruction; excerpt stays on the GitHub issue.
    body = {k: v for k, v in dict(payload).items() if k != "coder_brief_excerpt"}
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CerebrumFactory",
    }
    auth = webhook_authorization_header(env)
    if auth:
        headers["Authorization"] = auth
    req = Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with opener(req, timeout=15.0) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            resp.read()
    except HTTPError as exc:
        raise BuildsPushError(f"domain handoff webhook HTTP {exc.code}") from exc
    except URLError as exc:
        raise BuildsPushError(f"domain handoff webhook down: {exc.reason}") from exc
    if status >= 400:
        raise BuildsPushError(f"domain handoff webhook HTTP {status}")
    return True


def notify_domain_handoff(
    output_dir: Path | str,
    *,
    session_id: Optional[str] = None,
    product_id: Optional[str] = None,
    vertical: Optional[str] = None,
    blueprint: Any = None,
    plan: Any = None,
    blocks_root: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    force: bool = False,
) -> HandoffResult:
    """Fire post-CLONER finance handoff once. No-op for non-finance."""
    root = Path(output_dir)
    blob: Mapping[str, str] = env if env is not None else os.environ
    if not is_finance_domain(
        root, product_id=product_id, vertical=vertical, blueprint=blueprint
    ):
        return HandoffResult(
            fired=False, skipped=True, reason="not finance domain", domain=""
        )
    if not force and _already_fired(root):
        return HandoffResult(
            fired=False,
            skipped=True,
            already=True,
            reason="domain handoff already fired",
            domain="finance",
        )

    brief = ensure_coder_brief(
        root, blueprint=blueprint, plan=plan, blocks_root=blocks_root
    )
    payload = build_handoff_payload(
        root,
        session_id=session_id,
        env=blob,
        product_id=product_id,
        brief_path=brief,
    )

    issue_url = ""
    issue_number: Optional[int] = None
    webhook_posted = False
    errors: List[str] = []

    try:
        issue = open_or_update_handoff_issue(payload, env=blob, opener=opener)
        issue_url = str(issue.get("html_url") or "")
        issue_number = int(issue["number"]) if issue.get("number") else None
        payload = dict(payload)
        payload["issue_url"] = issue_url
        payload["issue_number"] = issue_number
    except Exception as exc:  # noqa: BLE001
        logger.exception("domain handoff: GitHub issue failed")
        errors.append(f"issue: {exc}")

    try:
        webhook_posted = post_webhook(payload, env=blob, opener=opener)
    except Exception as exc:  # noqa: BLE001
        logger.exception("domain handoff: webhook failed")
        errors.append(f"webhook: {exc}")

    # Stamp idempotency only when at least one channel reached MR.FINANCE.
    fired = bool(issue_url) or webhook_posted
    if fired:
        write_local_handoff(root, payload)
        _ledger_note(root, payload)
    return HandoffResult(
        fired=fired,
        skipped=False,
        reason="; ".join(errors) if errors else "ok",
        domain="finance",
        issue_url=issue_url,
        issue_number=issue_number,
        webhook_posted=webhook_posted,
        payload=dict(payload),
    )


def handoff_after_cloner(ctx: Any, *, env: Optional[Mapping[str, str]] = None) -> HandoffResult:
    """Wire point from ``run_cloner`` — best-effort, never fails CLONER."""
    dest = getattr(getattr(ctx, "workspace", None), "destination", None)
    if dest is None:
        dest = getattr(getattr(ctx, "workspace", None), "workspace", None)
    if dest is None:
        return HandoffResult(fired=False, skipped=True, reason="no workspace")
    root = Path(dest)
    state = getattr(ctx, "state", None)
    if not isinstance(state, dict):
        state = {}
    bp = getattr(ctx, "blueprint", None)
    product_id = str(
        getattr(bp, "product_id", None)
        or state.get("product_id")
        or root.name
        or ""
    )
    vertical = str(getattr(bp, "vertical", None) or state.get("vertical") or "")
    session_id = str(state.get("session_id") or "") or None
    try:
        return notify_domain_handoff(
            root,
            session_id=session_id,
            product_id=product_id,
            vertical=vertical,
            blueprint=bp,
            plan=getattr(ctx, "plan", None),
            blocks_root=getattr(ctx, "blocks_root", None),
            env=env,
        )
    except Exception:  # noqa: BLE001
        logger.exception("post-CLONER domain handoff failed at %s", root)
        return HandoffResult(
            fired=False, skipped=True, reason="handoff exception", domain="finance"
        )
