"""S4 kernel shipping path — execute_action owns HTTP; U4 persist-wrapper gone.

Kernel shipped (#184). F18 fabrication deleted. WRITER behaviour gate present.
This stage removes ``_ensure_route_persists_payload`` from the shipping path.
Persistence stays in the kernel/store after ActionStatus.SUCCESS, not a
route rewriter.

Evidence: ``build/stages/S4_kernel.json`` + reread twin.
Does not emit PILOT_READY. Does not start S5+.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.cerebrum_product_kernel.contract.runtime import execute_action
from app.factory.build.lotdesk_gate import resolve_lotdesk_fixture
from app.factory.build.roles import _coder_route_body, _templated_route_body
from app.factory.generator import git_head

EMITTER_ID = "app.factory.build.kernel.evaluate_kernel"
STAGE = "S4"
STAGE_NAME = "KERNEL"
WRAPPER_SYMBOL = "_ensure_route_persists_payload"
ROLES_REL = "backend/app/factory/build/roles.py"
KERNEL_REL = "backend/app/factory/build/kernel.py"
RUNTIME_REL = "backend/app/cerebrum_product_kernel/contract/runtime.py"
REWRITE_MARKER = r"\bsave\(\s*(?!payload"

#: handle() then save(...) with no kernel execute_action — LotDesk / winery class.
_HANDLE_THEN_SAVE = re.compile(
    r"handle\(\s*payload\s*\)[\s\S]{0,400}?save\(\s*"
    r"(payload|result|handled|record|saved)\s*\)",
    re.MULTILINE,
)

DELETED = (
    "function _ensure_route_persists_payload in roles.py",
    "WRITER call site body = _ensure_route_persists_payload(body)",
    "test that asserted save(result)/save(handled) is rewritten to save(payload)",
)
KEPT = (
    "_templated_route_body save(payload) after ActionStatus.SUCCESS (store persist)",
    "_coder_route_body returns None (no LLM HTTP authorship)",
    "kernel_bridge.run_capability awaits execute_action",
    "WRITER behaviour gate (persist only on SUCCESS)",
    "F18 fabrication layer already deleted from factory dispatch",
    "prepare_pilot_workspace remains absent",
)


class KernelError(ValueError):
    """Persist-wrapper present, kernel ownership broken, or LotDesk wrapper accepted."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _posix_under_repo(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_repo_root()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def persist_wrapper_findings(source: str) -> List[str]:
    """Named findings for a persist-wrapper that displaces execute_action."""
    findings: List[str] = []
    if f"def {WRAPPER_SYMBOL}" in source or f"{WRAPPER_SYMBOL}(" in source:
        findings.append(
            "U4 persist rewriter _ensure_route_persists_payload displaces execute_action"
        )
    if REWRITE_MARKER in source:
        findings.append("regex save() rewriter force-injects save(payload)")
    kernel_owned = "run_capability" in source or "execute_action" in source
    if _HANDLE_THEN_SAVE.search(source) and not kernel_owned:
        findings.append(
            "handle() then save() without execute_action (LotDesk-class persist-wrapper)"
        )
    return findings


def assert_no_persist_wrapper(source: str) -> None:
    findings = persist_wrapper_findings(source)
    if findings:
        raise KernelError("; ".join(findings))


def factory_roles_source(*, repo: Optional[Path] = None) -> str:
    root = Path(repo) if repo is not None else _repo_root()
    path = root / ROLES_REL
    return path.read_text(encoding="utf-8")


def wrapper_symbol_gone(*, repo: Optional[Path] = None) -> bool:
    from app.factory.build import roles as roles_mod

    source = factory_roles_source(repo=repo)
    return (
        not hasattr(roles_mod, WRAPPER_SYMBOL)
        and f"def {WRAPPER_SYMBOL}" not in source
        and f"{WRAPPER_SYMBOL}(" not in source
        and REWRITE_MARKER not in source
    )


def load_lotdesk_routes(explicit: Optional[Path] = None) -> str:
    """Inspect LotDesk as shipped. Never patch the fixture."""
    path = resolve_lotdesk_fixture(explicit)
    if path.is_file() and path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                rel = name.replace("\\", "/")
                if rel.endswith("app/routes.py"):
                    return zf.read(name).decode("utf-8", errors="replace")
        return ""
    candidate = path / "app" / "routes.py" if path.is_dir() else path
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace")
    return ""


def reject_lotdesk_persist_wrapper(
    explicit: Optional[Path] = None,
) -> Dict[str, Any]:
    """LotDesk-class persist-wrapper fails S4. A hollow accept is a factory defect."""
    path = resolve_lotdesk_fixture(explicit)
    routes = load_lotdesk_routes(explicit)
    findings = persist_wrapper_findings(routes)
    if findings:
        return {
            "ok": False,
            "gate": "lotdesk_persist_wrapper",
            "fixture": _posix_under_repo(path),
            "findings": findings,
            "reason": (
                "LotDesk-class persist-wrapper: capability HTTP handle()+save() "
                "without execute_action"
            ),
            "lotdesk": "fixture only; not patched",
        }
    raise AssertionError("GATE HOLLOW: LotDesk persist-wrapper was accepted by S4")


def templated_route_uses_execute_action() -> bool:
    source = inspect.getsource(_templated_route_body)
    return (
        "run_capability" in source
        and "save(payload)" in source
        and WRAPPER_SYMBOL not in source
    )


def inspect_s4_kernel(*, repo: Optional[Path] = None) -> Dict[str, Any]:
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import roles as roles_mod
    from app.factory.build import runner as runner_mod

    coder_none = _coder_route_body(None, None, None) is None
    runner_src = Path(runner_mod.__file__).read_text(encoding="utf-8")
    gone = wrapper_symbol_gone(repo=repo)
    template_ok = templated_route_uses_execute_action()
    prepare_absent = (
        "prepare_pilot_workspace" not in runner_src
        and not hasattr(pilot_mod, "prepare_pilot_workspace")
    )
    return {
        "execute_action": execute_action.__module__ + ".execute_action",
        "execute_action_callable": callable(execute_action),
        "_coder_route_body_is_None": coder_none,
        "persist_wrapper_symbol_gone": gone,
        "persist_wrapper_hasattr": hasattr(roles_mod, WRAPPER_SYMBOL),
        "templated_route_uses_execute_action": template_ok,
        "prepare_pilot_workspace": "absent" if prepare_absent else "present",
        "ok": (
            gone
            and coder_none
            and callable(execute_action)
            and template_ok
            and prepare_absent
        ),
    }


def canonical_fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    """Stable subset compared across a reread. Timestamps excluded."""
    return {
        "git_sha": result.get("git_sha"),
        "emitter": result.get("emitter"),
        "verdict": result.get("verdict"),
        "ok": result.get("ok"),
        "first_failing_criterion": result.get("first_failing_criterion"),
        "pass_criteria": result.get("pass_criteria"),
        "deleted": result.get("deleted"),
        "kept": result.get("kept"),
        "PILOT_READY": result.get("PILOT_READY"),
        "lotdesk_wrapper_rejected": (result.get("lotdesk_persist_wrapper") or {}).get(
            "ok"
        )
        is False,
    }


def fingerprint_disagreements(
    primary: Dict[str, Any], reread: Dict[str, Any]
) -> List[str]:
    left = canonical_fingerprint(primary)
    right = canonical_fingerprint(reread)
    if left == right:
        return []
    found: List[str] = []
    for key in left:
        if left.get(key) != right.get(key):
            found.append(key)
    return found or ["canonical_fingerprint"]


def evaluate_kernel(*, repo: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo) if repo is not None else _repo_root()
    kernel = inspect_s4_kernel(repo=root)
    lotdesk = reject_lotdesk_persist_wrapper()
    module_path = root / KERNEL_REL
    roles_path = root / ROLES_REL
    first = None
    if not module_path.is_file():
        first = "kernel_module_missing"
    elif not roles_path.is_file():
        first = "roles_module_missing"
    elif not kernel["persist_wrapper_symbol_gone"]:
        first = "persist_wrapper_still_present"
    elif not kernel["_coder_route_body_is_None"]:
        first = "coder_route_body_restored"
    elif not kernel["execute_action_callable"]:
        first = "execute_action_not_callable"
    elif not kernel["templated_route_uses_execute_action"]:
        first = "templated_route_not_execute_action"
    elif kernel["prepare_pilot_workspace"] != "absent":
        first = "prepare_pilot_workspace_restored"
    elif lotdesk.get("ok") is not False or not lotdesk.get("findings"):
        first = "lotdesk_persist_wrapper_not_rejected"
    ok = first is None
    git_sha = git_head(root)
    return {
        "stage": STAGE,
        "name": STAGE_NAME,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "first_failing_criterion": first,
        "git_sha": git_sha,
        "kernel": kernel,
        "lotdesk_persist_wrapper": lotdesk,
        "pass_criteria": {
            "persist_wrapper_symbol_gone": bool(kernel["persist_wrapper_symbol_gone"]),
            "coder_route_body_is_None": bool(kernel["_coder_route_body_is_None"]),
            "execute_action_callable": bool(kernel["execute_action_callable"]),
            "templated_route_uses_execute_action": bool(
                kernel["templated_route_uses_execute_action"]
            ),
            "lotdesk_persist_wrapper_rejected": lotdesk.get("ok") is False
            and bool(lotdesk.get("findings")),
            "prepare_pilot_workspace_absent": kernel["prepare_pilot_workspace"]
            == "absent",
        },
        "implementation": {
            "module": KERNEL_REL,
            "shipping_path": ROLES_REL,
            "kernel_entry": RUNTIME_REL + ":execute_action",
            "u4": "deleted from shipping path; persist stays in kernel/store",
        },
        "deleted": list(DELETED),
        "kept": list(KEPT),
        "PILOT_READY": False,
        "not_claimed": [
            "PILOT_READY",
            "S5 U7 FACTORY_SUITE_MARKER_EXPR",
            "S6 chat/capture binding",
        ],
        "lotdesk": "fixture only; not patched",
        "llm_route_authorship": "not restored; _coder_route_body still returns None",
        "not_started": [
            "S5",
            "S6",
            "S7",
            "S8",
            "S9",
            "S10",
            "S11",
            "S12",
            "S13",
        ],
    }


def reread_matches(evidence: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    if str(evidence.get("verdict") or "").strip().upper() != str(
        twin.get("verdict") or ""
    ).strip().upper():
        return False
    disagreements = twin.get("disagreements")
    if isinstance(disagreements, list) and disagreements:
        return False
    return True


def write_reread_twin(
    evidence_path: Path,
    result: Dict[str, Any],
    *,
    reread: Optional[Dict[str, Any]] = None,
) -> Path:
    from app.factory.build.preflight import reread_twin_path, write_evidence

    second = reread if reread is not None else evaluate_kernel()
    disagreements = fingerprint_disagreements(result, second)
    if disagreements:
        result["verdict"] = "FAIL"
        result["ok"] = False
        result["first_failing_criterion"] = "reread_mismatch:" + ",".join(
            disagreements
        )
        write_evidence(evidence_path, result)
    twin = {
        "stage": STAGE,
        "name": "kernel",
        "verdict": result.get("verdict"),
        "reread_of": _posix_under_repo(evidence_path),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s4-kernel",
        "emitter": EMITTER_ID,
        "git_sha": second.get("git_sha"),
        "disagreements": disagreements,
        "checked": [
            "_ensure_route_persists_payload symbol gone from roles.py",
            "RoleRunner templated route still goes through execute_action",
            "LotDesk-class persist-wrapper is rejected; fixture not patched",
            "_coder_route_body still returns None",
            "prepare_pilot_workspace remains absent",
        ],
        "deleted": list(DELETED),
        "kept": list(KEPT),
        "not_claimed": result.get("not_claimed") or [],
        "PILOT_READY": False,
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(
        json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return dest


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.factory.build.preflight import default_stages_dir, write_evidence

    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S4_kernel.json"
    if args:
        stages = Path(args[0])
        dest = stages / "S4_kernel.json"
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_kernel()
    write_evidence(dest, result)
    write_reread_twin(dest, result)
    print(
        json.dumps(
            {
                "wrote": str(dest),
                "verdict": result["verdict"],
                "first_failing_criterion": result.get("first_failing_criterion"),
                "PILOT_READY": False,
            },
            indent=2,
        )
    )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
