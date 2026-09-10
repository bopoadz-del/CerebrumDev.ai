"""Store-green = scripts/acceptance.py (≥12 measured checks) in the Store image.

Authorship floor is not acceptance. HTTP 200 ok:false is not a pass.
Export / Store-green require k/k of the twelve named lines.

RAG skip policy (documented): when the product has no RAG surface the
``rag_roundtrip_hit`` line is ``SKIP:no-rag-surface`` and counts as a
satisfied line. Steward (or any product with a RAG route/capability) must
plant a paragraph and get a hit — skip is forbidden there.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "store_acceptance"
ACCEPTANCE_REQUIRED = 12
ACCEPTANCE_SCRIPT_REL = Path("scripts") / "acceptance.py"
ACCEPTANCE_REPORT_REL = Path("docs") / "store_acceptance.json"
OPENAPI_REL = Path("docs") / "openapi.json"
GITHUB_CI_REL = Path(".github") / "workflows" / "ci.yml"
UI_INDEX_REL = Path("app") / "static" / "index.html"
AUTH_REL = Path("app") / "auth.py"

ACCEPTANCE_CHECK_NAMES: tuple[str, ...] = (
    "no_token_401",
    "missing_field_422",
    "enum_422",
    "ui_served_200",
    "rag_roundtrip_hit",
    "single_persistence_root",
    "ci_present_and_full_suite",
    "handler_bodies_distinct",
    "health_fail_closed",
    "openapi_committed",
    "docker_health_200",
    "authorship_floor",
)

assert len(ACCEPTANCE_CHECK_NAMES) >= ACCEPTANCE_REQUIRED
assert ACCEPTANCE_CHECK_NAMES[-1] == "authorship_floor"

LINE_RE = re.compile(
    r"^(PASS|FAIL|SKIP)\s+([a-z0-9_]+)\s*(?:—|-|:)?\s*(.*)$",
    re.IGNORECASE,
)
SUMMARY_RE = re.compile(r"ACCEPTANCE:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)


@dataclass
class AcceptanceLine:
    name: str
    status: str
    detail: str = ""

    @property
    def satisfied(self) -> bool:
        return self.status in {"PASS", "SKIP"}


@dataclass
class AcceptanceReport:
    passed: int = 0
    total: int = ACCEPTANCE_REQUIRED
    ok: bool = False
    lines: List[AcceptanceLine] = field(default_factory=list)
    missing: bool = False
    via: str = "scripts/acceptance.py"
    detail: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "schema_version": "store_acceptance.v1",
            "passed": self.passed,
            "total": self.total,
            "ok": self.ok,
            "missing": self.missing,
            "via": self.via,
            "detail": self.detail,
            "score": f"{self.passed}/{self.total}",
            "lines": [asdict(line) for line in self.lines],
            "checks": list(ACCEPTANCE_CHECK_NAMES),
        }


def missing_acceptance_report(*, detail: str = "scripts/acceptance.py was not run") -> AcceptanceReport:
    return AcceptanceReport(
        passed=0,
        total=ACCEPTANCE_REQUIRED,
        ok=False,
        missing=True,
        detail=detail,
        lines=[
            AcceptanceLine(name=name, status="FAIL", detail="not measured")
            for name in ACCEPTANCE_CHECK_NAMES
        ],
    )


def parse_acceptance_output(text: str) -> AcceptanceReport:
    """Parse harness stdout. Presence of ok:true in a product is not a pass."""
    lines: List[AcceptanceLine] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        match = LINE_RE.match(raw.strip())
        if not match:
            continue
        status, name, detail = match.group(1).upper(), match.group(2), match.group(3).strip()
        if name not in ACCEPTANCE_CHECK_NAMES or name in seen:
            continue
        seen.add(name)
        lines.append(AcceptanceLine(name=name, status=status, detail=detail))
    by_name = {line.name: line for line in lines}
    ordered = [
        by_name.get(
            name,
            AcceptanceLine(name=name, status="FAIL", detail="harness omitted this line"),
        )
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    satisfied = sum(1 for line in ordered if line.satisfied)
    summary = SUMMARY_RE.search(text or "")
    total = ACCEPTANCE_REQUIRED
    if summary:
        total = max(int(summary.group(2)), ACCEPTANCE_REQUIRED)
    ok = (
        satisfied >= ACCEPTANCE_REQUIRED
        and total >= ACCEPTANCE_REQUIRED
        and all(line.satisfied for line in ordered)
        and ordered[-1].name == "authorship_floor"
    )
    return AcceptanceReport(
        passed=satisfied,
        total=total,
        ok=ok,
        lines=ordered,
        detail=f"{satisfied}/{total}",
    )


def write_acceptance_report(root: Path | str, report: AcceptanceReport) -> Path:
    dest = Path(root) / ACCEPTANCE_REPORT_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def read_acceptance_report(
    root: Optional[Path | str] = None,
    status: Optional[Mapping[str, Any]] = None,
) -> AcceptanceReport:
    """Fail-closed: missing report is 0/12, not a pass."""
    if status:
        raw = status.get("acceptance")
        if isinstance(raw, Mapping) and raw.get("missing") is not True:
            return _report_from_mapping(raw)
        grade = status.get("level_grade")
        if isinstance(grade, Mapping) and isinstance(grade.get("acceptance"), Mapping):
            return _report_from_mapping(grade["acceptance"])
    if root:
        path = Path(root) / ACCEPTANCE_REPORT_REL
        if path.is_file():
            try:
                return _report_from_mapping(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return missing_acceptance_report(detail="docs/store_acceptance.json is unreadable")
    return missing_acceptance_report()


def _report_from_mapping(raw: Mapping[str, Any]) -> AcceptanceReport:
    lines: List[AcceptanceLine] = []
    for item in raw.get("lines") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        if name not in ACCEPTANCE_CHECK_NAMES:
            continue
        lines.append(
            AcceptanceLine(
                name=name,
                status=str(item.get("status") or "FAIL").upper(),
                detail=str(item.get("detail") or ""),
            )
        )
    if not lines:
        return parse_acceptance_output(str(raw.get("detail") or ""))
    by_name = {line.name: line for line in lines}
    ordered = [
        by_name.get(name, AcceptanceLine(name=name, status="FAIL", detail="omitted"))
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    passed = int(raw.get("passed") or sum(1 for line in ordered if line.satisfied))
    total = int(raw.get("total") or ACCEPTANCE_REQUIRED)
    if total < ACCEPTANCE_REQUIRED:
        total = ACCEPTANCE_REQUIRED
    ok = bool(raw.get("ok")) and passed >= total and all(line.satisfied for line in ordered)
    return AcceptanceReport(
        passed=passed,
        total=total,
        ok=ok,
        lines=ordered,
        missing=bool(raw.get("missing")),
        via=str(raw.get("via") or "scripts/acceptance.py"),
        detail=str(raw.get("detail") or f"{passed}/{total}"),
    )


def acceptance_is_kk(report: Optional[AcceptanceReport]) -> bool:
    if report is None or report.missing:
        return False
    return (
        report.ok
        and report.passed >= ACCEPTANCE_REQUIRED
        and report.total >= ACCEPTANCE_REQUIRED
        and report.passed == report.total
    )


def acceptance_export_blocker(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
) -> Optional[str]:
    """Refuse Store-green / Export unless acceptance is k/k.

    Code-cycle prototypes (PRODUCT/STORE not run) are not this lie.
    Authorship-only green is not acceptance.
    """
    status = dict(status or {})
    cycle = str(status.get("cycle") or "").strip().lower()
    grade = status.get("level_grade")
    gates = grade.get("three_gate") if isinstance(grade, Mapping) else None
    if not isinstance(gates, Mapping):
        from app.factory.build.level_grade import parse_three_gate_verdict

        gates = parse_three_gate_verdict(str(status.get("detail") or ""))
    claiming = cycle == "pilot" or (
        str(gates.get("PRODUCT") or "") == "PASS" and str(gates.get("STORE") or "") == "PASS"
    )
    if not claiming:
        return None
    report = read_acceptance_report(workspace, status)
    if acceptance_is_kk(report):
        return None
    return (
        f"STORE_ACCEPTANCE: acceptance is {report.passed}/{report.total} "
        "(Export / Store-green require k/k PASS of scripts/acceptance.py; "
        "authorship floor is not acceptance)"
    )


def render_auth_module() -> str:
    return '''"""Capability write routes require a platform token.

POST /v1/<capability> without a bearer / X-Platform-Token is HTTP 401.
Missing required fields and invalid enums are HTTP 422. JSON ``ok: false``
with HTTP 200 is not an auth or validation pass.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import HTTPException, Request

PLATFORM_TOKEN_ENV = "PLATFORM_TOKEN"
DEFAULT_PLATFORM_TOKEN = "dev-local-token"


def platform_token() -> str:
    return (os.environ.get(PLATFORM_TOKEN_ENV) or DEFAULT_PLATFORM_TOKEN).strip()


def require_platform_token(request: Request) -> str:
    expected = platform_token()
    header = request.headers.get("authorization") or ""
    token = (request.headers.get("x-platform-token") or "").strip()
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="authentication_required")
    if token != expected:
        raise HTTPException(status_code=401, detail="authentication_required")
    return token


def reject_invalid_payload(capability_id: str, payload: Dict[str, Any] | None) -> None:
    from app.models import MODELS

    cls = MODELS.get(capability_id)
    if cls is None:
        raise HTTPException(status_code=422, detail="unknown capability")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")
    fields = list(getattr(cls, "FIELDS", []) or [])
    constraints = getattr(cls, "CONSTRAINTS", {}) or {}
    required = [
        name
        for name in fields
        if (constraints.get(name) or {}).get("required")
        or name in getattr(cls, "REQUIRED", ())
    ]
    if not required:
        # Models stamp required on the field spec; fall back to every field
        # that has no default in CONSTRAINTS.required=False only.
        required = [
            name
            for name in fields
            if (constraints.get(name) or {}).get("required") is not False
            and name in constraints
            and constraints[name].get("required")
        ]
    for name in required:
        if name not in payload or payload[name] in (None, ""):
            raise HTTPException(
                status_code=422, detail="Missing required field: " + name
            )
    for name, rules in constraints.items():
        if name not in payload:
            continue
        allowed = rules.get("allowed_values")
        if allowed is not None and payload[name] not in allowed:
            raise HTTPException(
                status_code=422,
                detail=name + " must be one of: " + ", ".join(str(v) for v in allowed),
            )
'''


def render_github_ci() -> str:
    return (
        "# Full suite — python -m pytest tests. Store-green measures this file.\n"
        "name: ci\n"
        "on:\n"
        "  push:\n"
        "  pull_request:\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "3.12"\n'
        "      - run: pip install -r requirements.txt -r requirements-dev.txt\n"
        "      - run: python -m pytest tests\n"
    )


def render_openapi(product_name: str, cap_ids: Sequence[str]) -> str:
    paths: Dict[str, Any] = {
        "/": {"get": {"responses": {"200": {"description": "served UI"}}}},
        "/health": {"get": {"responses": {"200": {"description": "fail-closed health"}}}},
    }
    for cap in cap_ids:
        name = str(cap).replace("-", "_")
        paths[f"/v1/{name}"] = {
            "post": {
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "accepted"},
                    "401": {"description": "no token"},
                    "422": {"description": "validation"},
                },
            },
            "get": {"responses": {"200": {"description": "list"}}},
        }
    doc = {
        "openapi": "3.0.3",
        "info": {"title": product_name or "platform", "version": "1.0.0"},
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def render_ui_index(product_name: str) -> str:
    title = product_name or "Platform"
    return (
        "<!doctype html>\n"
        f"<html lang=\"en\"><head><meta charset=\"utf-8\"><title>{title}</title></head>\n"
        f"<body><main><h1>{title}</h1><p>Generated platform UI.</p></main></body></html>\n"
    )


def render_acceptance_script() -> str:
    """Self-contained harness stamped into every pilot zip."""
    names = ", ".join(repr(n) for n in ACCEPTANCE_CHECK_NAMES)
    return f'''#!/usr/bin/env python3
"""Store-green acceptance — ≥12 measured checks. Presence-only is a fail.

Authorship floor is the LAST line. HTTP 200 ok:false is not a pass.
RAG skip policy: SKIP:no-rag-surface only when the product has no RAG
route or rag capability. Steward must plant + hit.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [{names}]
REQUIRED = {ACCEPTANCE_REQUIRED}


def _token() -> str:
    return (os.environ.get("PLATFORM_TOKEN") or "dev-local-token").strip()


def _auth() -> Dict[str, str]:
    return {{"Authorization": "Bearer " + _token()}}


class _Http:
    def __init__(self, client: Any):
        self.client = client

    def request(self, method: str, path: str, **kw: Any) -> Any:
        fn = getattr(self.client, method.lower())
        return fn(path, **kw)


def _client() -> Tuple[_Http, Any]:
    base = (os.environ.get("ACCEPTANCE_BASE_URL") or "").rstrip("/")
    if base:
        import urllib.error
        import urllib.request

        class _Url:
            def request(self, method: str, path: str, json=None, headers=None, **_kw):
                data = None
                hdrs = dict(headers or {{}})
                if json is not None:
                    data = json_mod.dumps(json).encode("utf-8")
                    hdrs.setdefault("Content-Type", "application/json")
                req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method.upper())
                try:
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        body = resp.read()
                        return _Resp(resp.status, body, resp.headers)
                except urllib.error.HTTPError as exc:
                    return _Resp(exc.code, exc.read(), exc.headers)

        import json as json_mod

        class _Resp:
            def __init__(self, status, body, headers):
                self.status_code = int(status)
                self._body = body or b""
                self.headers = headers or {{}}
                self.content = self._body

            def json(self):
                return json.loads(self._body.decode("utf-8") or "{{}}")

            @property
            def text(self) -> str:
                return self._body.decode("utf-8", errors="replace")

        url = _Url()

        class _Wrap:
            def get(self, path, **kw):
                return url.request("GET", path, **kw)

            def post(self, path, **kw):
                return url.request("POST", path, **kw)

        return _Http(_Wrap()), None

    from fastapi.testclient import TestClient
    from app.main import app

    cm = TestClient(app)
    client = cm.__enter__()
    return _Http(client), cm


def _first_cap() -> str:
    receipt = ROOT / "docs" / "coder_receipt.json"
    if receipt.is_file():
        try:
            data = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {{}}
        caps = data.get("capabilities") or []
        if caps:
            first = caps[0]
            if isinstance(first, dict):
                return str(first.get("id") or first.get("capability_id") or "")
            return str(first)
    try:
        from app.jobs import CAPABILITIES

        for item in CAPABILITIES or []:
            if isinstance(item, dict) and item.get("id"):
                return str(item["id"])
            if isinstance(item, str) and item.strip():
                return item
    except Exception:
        pass
    try:
        from app.models import MODELS

        if MODELS:
            return sorted(MODELS)[0]
    except Exception:
        pass
    return ""


def _models():
    try:
        from app.models import MODELS

        return MODELS
    except Exception:
        return {{}}


def _required_and_enum(cap_id: str) -> Tuple[Optional[str], Optional[Tuple[str, List[Any]]]]:
    models = _models()
    cls = models.get(cap_id) if cap_id else None
    required = None
    enum = None
    if cls is not None:
        fields = list(getattr(cls, "FIELDS", []) or [])
        constraints = getattr(cls, "CONSTRAINTS", {{}}) or {{}}
        for name in fields:
            rules = constraints.get(name) or {{}}
            if rules.get("required") and required is None:
                required = name
            allowed = rules.get("allowed_values") or []
            if allowed and enum is None:
                enum = (name, list(allowed))
    if required is None or enum is None:
        for other_id, other in models.items():
            fields = list(getattr(other, "FIELDS", []) or [])
            constraints = getattr(other, "CONSTRAINTS", {{}}) or {{}}
            for name in fields:
                rules = constraints.get(name) or {{}}
                if required is None and rules.get("required"):
                    required = name
                    cap_id = other_id
                if enum is None:
                    allowed = rules.get("allowed_values") or []
                    if allowed:
                        enum = (name, list(allowed))
    return required, enum


def _has_rag_surface() -> bool:
    for rel in (
        ROOT / "docs" / "rag" / "dual_rag.json",
        ROOT / "docs" / "coder_receipt.json",
    ):
        if not rel.is_file():
            continue
        try:
            blob = rel.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        if "rag" in blob:
            return True
    try:
        from app.models import MODELS

        if any("rag" in str(k).lower() for k in MODELS):
            return True
    except Exception:
        pass
    try:
        from app.jobs import CAPABILITIES

        for item in CAPABILITIES or []:
            ident = item.get("id") if isinstance(item, dict) else item
            if "rag" in str(ident).lower():
                return True
    except Exception:
        pass
    main = ROOT / "app" / "main.py"
    routes = ROOT / "app" / "routes.py"
    text = ""
    for path in (main, routes):
        if path.is_file():
            text += path.read_text(encoding="utf-8")
    return bool(re.search(r"/v1/(steward/)?rag|/v1/dual_rag", text))


def check_no_token_401(http: _Http) -> Tuple[str, str]:
    cap = _first_cap()
    if not cap:
        return "FAIL", "no first capability to POST"
    resp = http.request("post", "/v1/" + cap, json={{}})
    if resp.status_code == 401:
        return "PASS", "HTTP 401"
    if resp.status_code == 200:
        body = {{}}
        try:
            body = resp.json()
        except Exception:
            pass
        return "FAIL", "HTTP 200 ok:%s (must be 401, not ok:false)" % body.get("ok")
    return "FAIL", "HTTP %s (want 401)" % resp.status_code


def check_missing_field_422(http: _Http) -> Tuple[str, str]:
    cap = _first_cap()
    required, _enum = _required_and_enum(cap)
    if not cap:
        return "FAIL", "no capability"
    if not required:
        return "FAIL", "no required field to measure (not a presence skip)"
    resp = http.request("post", "/v1/" + cap, json={{}}, headers=_auth())
    if resp.status_code == 422:
        return "PASS", "HTTP 422 missing %s" % required
    return "FAIL", "HTTP %s (want 422 for missing %s)" % (resp.status_code, required)


def check_enum_422(http: _Http) -> Tuple[str, str]:
    cap = _first_cap()
    required, enum = _required_and_enum(cap)
    if not enum:
        return "FAIL", "no enum field to measure (not a presence skip)"
    name, allowed = enum
    payload: Dict[str, Any] = {{}}
    models = _models()
    cls = models.get(cap)
    if cls is not None:
        constraints = getattr(cls, "CONSTRAINTS", {{}}) or {{}}
        for field in getattr(cls, "FIELDS", []) or []:
            rules = constraints.get(field) or {{}}
            if rules.get("allowed_values"):
                payload[field] = rules["allowed_values"][0]
            elif rules.get("required"):
                payload[field] = "sample"
    payload[name] = "__not_in_contract__"
    resp = http.request("post", "/v1/" + cap, json=payload, headers=_auth())
    if resp.status_code == 422:
        return "PASS", "HTTP 422 invalid %s" % name
    return "FAIL", "HTTP %s (want 422 for invalid enum %s)" % (resp.status_code, name)


def check_ui_served_200(http: _Http) -> Tuple[str, str]:
    resp = http.request("get", "/")
    if resp.status_code != 200:
        return "FAIL", "GET / HTTP %s" % resp.status_code
    text = getattr(resp, "text", "") or ""
    ctype = ""
    headers = getattr(resp, "headers", {{}}) or {{}}
    if hasattr(headers, "get"):
        ctype = str(headers.get("content-type") or headers.get("Content-Type") or "")
    if "html" in ctype.lower() or "<html" in text.lower() or "<!doctype" in text.lower():
        return "PASS", "GET / HTTP 200 HTML"
    return "FAIL", "GET / was 200 but not served UI (content-type=%s)" % ctype


def check_rag_roundtrip_hit(http: _Http) -> Tuple[str, str]:
    if not _has_rag_surface():
        return "SKIP", "no-rag-surface"
    marker = "ACCEPTANCE-PLANT-%s copper kettle ordinance" % uuid.uuid4().hex[:8]
    ingest_paths = (
        "/v1/rag/ingest",
        "/v1/steward/rag/ingest",
        "/v1/dual_rag_sop",
        "/v1/dual_rag_estate_docs",
    )
    planted = False
    for path in ingest_paths:
        resp = http.request(
            "post",
            path,
            json={{"text": marker, "content": marker, "paragraph": marker, "query": marker}},
            headers=_auth(),
        )
        if resp.status_code in (200, 201, 202):
            planted = True
            break
    if not planted:
        return "FAIL", "RAG surface present but plant did not accept"
    query_paths = (
        "/v1/rag/query",
        "/v1/steward/rag/query",
        "/v1/rag/dual",
        "/v1/dual_rag_sop",
    )
    for path in query_paths:
        resp = http.request(
            "post",
            path,
            json={{"q": "copper kettle ordinance", "query": "copper kettle ordinance", "text": marker}},
            headers=_auth(),
        )
        if resp.status_code != 200:
            continue
        blob = (getattr(resp, "text", "") or "").lower()
        if "copper kettle" in blob or marker.lower() in blob:
            return "PASS", "plant+query hit via %s" % path
        try:
            data = resp.json()
        except Exception:
            data = {{}}
        hits = []
        if isinstance(data, dict):
            for key in ("hits", "results", "items", "matches", "chunks"):
                val = data.get(key)
                if isinstance(val, list) and val:
                    hits = val
        if hits:
            return "PASS", "plant+query returned %d hit(s) via %s" % (len(hits), path)
    return "FAIL", "RAG surface present but query missed"


def check_single_persistence_root() -> Tuple[str, str]:
    store = ROOT / "app" / "store.py"
    if not store.is_file():
        return "FAIL", "app/store.py missing"
    text = store.read_text(encoding="utf-8")
    env_hits = len(re.findall(r"STORAGE_PATH", text))
    db_names = set(re.findall(r"""['\"]([^'\"]+\\.db)['\"]""", text))
    if env_hits < 1:
        return "FAIL", "STORAGE_PATH not used"
    if len(db_names) > 1:
        return "FAIL", "multiple db files: " + ", ".join(sorted(db_names))
    extra_roots = [
        line
        for line in text.splitlines()
        if re.search(r"sqlite3\\.connect\\(|open\\(.*\\.db", line)
        and "STORAGE_PATH" not in line
        and "platform.db" not in line
        and not line.strip().startswith("#")
    ]
    if extra_roots:
        return "FAIL", "connect() outside STORAGE_PATH: " + extra_roots[0].strip()[:80]
    return "PASS", "one STORAGE_PATH root (%s)" % (next(iter(db_names), "platform.db"))


def check_ci_present_and_full_suite() -> Tuple[str, str]:
    ci = ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        return "FAIL", ".github/workflows/ci.yml missing"
    text = ci.read_text(encoding="utf-8")
    run_lines = [
        line
        for line in text.splitlines()
        if "pytest" in line and not line.lstrip().startswith("#")
    ]
    if not run_lines:
        return "FAIL", "CI does not invoke pytest"
    has_full = any(
        ("python -m pytest tests" in line or "pytest tests" in line)
        and "not pilot" not in line
        for line in run_lines
    )
    if has_full:
        return "PASS", "CI runs pytest tests"
    if any("not pilot" in line for line in run_lines):
        return "FAIL", "CI wires only pytest -m not-pilot — not the full suite"
    return "FAIL", "CI pytest line is not a full suite"


def check_handler_bodies_distinct() -> Tuple[str, str]:
    actions = ROOT / "app" / "actions"
    if not actions.is_dir():
        return "FAIL", "app/actions missing"
    bodies: Dict[str, List[str]] = {{}}
    for path in sorted(actions.glob("*.py")):
        if path.name.startswith("_"):
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return "FAIL", "%s does not parse" % path.name
        handle = None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "handle":
                handle = node
                break
        if handle is None:
            continue
        chunk = ast.get_source_segment(src, handle) or ast.dump(handle)
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        bodies.setdefault(digest, []).append(path.name)
    twins = [names for names in bodies.values() if len(names) > 1]
    if twins:
        return "FAIL", "identical handle() bodies: " + ", ".join(twins[0])
    if len(bodies) < 2:
        return "PASS", "single handler — nothing to clone"
    return "PASS", "%d distinct handle() bodies" % len(bodies)


def check_health_fail_closed() -> Tuple[str, str]:
    missing = ROOT / ".acceptance-missing-disk"
    os.environ["STORAGE_PATH"] = str(missing)
    try:
        from app.health import evaluate_health

        code, body = evaluate_health()
    except Exception as exc:
        return "FAIL", "evaluate_health raised %s" % type(exc).__name__
    if code == 200 or (isinstance(body, dict) and body.get("ok") is True):
        return "FAIL", "health stayed 200/ok when STORAGE_PATH is missing"
    if int(code) in (503, 500) and (not body.get("ok")):
        return "PASS", "HTTP %s fail-closed" % code
    return "FAIL", "health code=%s ok=%s" % (code, (body or {{}}).get("ok"))


def check_openapi_committed() -> Tuple[str, str]:
    path = ROOT / "docs" / "openapi.json"
    if not path.is_file():
        return "FAIL", "docs/openapi.json missing"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return "FAIL", "openapi is not JSON: %s" % exc
    if not str(doc.get("openapi") or "").startswith("3."):
        return "FAIL", "openapi version is not 3.x"
    paths = doc.get("paths")
    if not isinstance(paths, dict) or "/health" not in paths:
        return "FAIL", "openapi paths omit /health"
    v1 = [p for p in paths if str(p).startswith("/v1/")]
    if not v1:
        return "FAIL", "openapi has no /v1/ paths"
    return "PASS", "openapi 3.x with %d paths" % len(paths)


def check_docker_health_200(http: _Http) -> Tuple[str, str]:
    measured = (os.environ.get("STORE_DOCKER_HEALTH") or "").strip()
    if measured != "200":
        return "FAIL", "STORE_DOCKER_HEALTH=%r (Store gate must measure container /health=200)" % measured
    resp = http.request("get", "/health")
    if resp.status_code != 200:
        return "FAIL", "container health env=200 but GET /health is %s" % resp.status_code
    return "PASS", "docker health 200"


def check_authorship_floor() -> Tuple[str, str]:
    from app.factory.build.authorship import (  # type: ignore
        full_pilot_authorship_from,
    )

    # Prefer in-tree provenance so the product can judge itself without the
    # factory. Fall back to counting action modules tagged agent-written.
    receipt = {{}}
    for rel in ("docs/coder_receipt.json", "docs/build_provenance.json"):
        path = ROOT / rel
        if path.is_file():
            try:
                receipt.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    try:
        floor = full_pilot_authorship_from(receipt, ROOT)
        if floor.meets_floor:
            return "PASS", "need≥%s action_py=%s cli=%s" % (
                floor.need,
                floor.action_py,
                len(floor.cli_authored_ids),
            )
        return "FAIL", "below floor need≥%s action_py=%s" % (floor.need, floor.action_py)
    except Exception:
        pass
    authored = 0
    actions = ROOT / "app" / "actions"
    if actions.is_dir():
        for path in actions.glob("*.py"):
            if path.name.startswith("_"):
                continue
            text = path.read_text(encoding="utf-8")
            if "CODER_MODEL" in text or "coding agent" in text.lower() or "coder CLI" in text:
                authored += 1
    n_required = receipt.get("n_required") or receipt.get("n_required_capabilities")
    try:
        n_required = int(n_required) if n_required is not None else None
    except (TypeError, ValueError):
        n_required = None
    need = 5 if n_required is None else min(5, max(1, int(n_required)))
    if authored >= need:
        return "PASS", "authored=%s need≥%s" % (authored, need)
    return "FAIL", "authored=%s below need≥%s" % (authored, need)


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    http, cm = _client()
    results: List[Tuple[str, str, str]] = []
    try:
        runners = [
            ("no_token_401", lambda: check_no_token_401(http)),
            ("missing_field_422", lambda: check_missing_field_422(http)),
            ("enum_422", lambda: check_enum_422(http)),
            ("ui_served_200", lambda: check_ui_served_200(http)),
            ("rag_roundtrip_hit", lambda: check_rag_roundtrip_hit(http)),
            ("single_persistence_root", check_single_persistence_root),
            ("ci_present_and_full_suite", check_ci_present_and_full_suite),
            ("handler_bodies_distinct", check_handler_bodies_distinct),
            ("health_fail_closed", check_health_fail_closed),
            ("openapi_committed", check_openapi_committed),
            ("docker_health_200", lambda: check_docker_health_200(http)),
            ("authorship_floor", check_authorship_floor),
        ]
        for name, fn in runners:
            try:
                status, detail = fn()
            except Exception as exc:
                status, detail = "FAIL", "%s: %s" % (type(exc).__name__, exc)
            results.append((name, status, detail))
            print("%s %s — %s" % (status, name, detail))
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass
    satisfied = sum(1 for _n, status, _d in results if status in {{"PASS", "SKIP"}})
    print("ACCEPTANCE: %d/%d" % (satisfied, REQUIRED))
    if results and results[-1][0] != "authorship_floor":
        print("FAIL harness — authorship_floor was not last")
        return 1
    if satisfied < REQUIRED or any(status == "FAIL" for _n, status, _d in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def stamp_acceptance_artifacts(
    workspace: Any,
    *,
    product_name: str,
    cap_ids: Sequence[str],
) -> None:
    """WRITER / ProductGenerator emit the harness and the files it measures."""
    workspace.write_text(ACCEPTANCE_SCRIPT_REL, render_acceptance_script())
    workspace.write_text(AUTH_REL, render_auth_module())
    workspace.write_text(GITHUB_CI_REL, render_github_ci())
    workspace.write_text(OPENAPI_REL, render_openapi(product_name, cap_ids))
    workspace.write_text(UI_INDEX_REL, render_ui_index(product_name))


def stamp_acceptance_into_path(
    root: Path | str,
    *,
    product_name: str = "platform",
    cap_ids: Optional[Sequence[str]] = None,
) -> None:
    dest = Path(root)
    files = {
        ACCEPTANCE_SCRIPT_REL: render_acceptance_script(),
        AUTH_REL: render_auth_module(),
        GITHUB_CI_REL: render_github_ci(),
        OPENAPI_REL: render_openapi(product_name, cap_ids or ()),
        UI_INDEX_REL: render_ui_index(product_name),
    }
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def evaluate_acceptance(
    root: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    python: Optional[str] = None,
    docker_health: Optional[str] = None,
) -> AcceptanceReport:
    """Run the stamped harness (or a temp copy) against *root*."""
    dest = Path(root)
    script = dest / ACCEPTANCE_SCRIPT_REL
    if not script.is_file():
        stamp_acceptance_into_path(dest)
        script = dest / ACCEPTANCE_SCRIPT_REL
    run_env = {**os.environ, **dict(env or {})}
    if docker_health is not None:
        run_env["STORE_DOCKER_HEALTH"] = str(docker_health)
    import subprocess

    proc = subprocess.run(
        [python or sys_executable(), str(script)],
        cwd=str(dest),
        capture_output=True,
        text=True,
        env=run_env,
        timeout=120,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    report = parse_acceptance_output(text)
    if proc.returncode != 0 and report.ok:
        report.ok = False
        report.detail += "; process exited %s" % proc.returncode
    return report


def sys_executable() -> str:
    import sys

    return sys.executable


def _docker_available(ctx: "GateContext") -> bool:
    if ctx.runner is not None:
        return True
    return shutil.which("docker") is not None


def gate_store_acceptance(ctx: "GateContext") -> "GateResult":
    """STORE: run scripts/acceptance.py inside the Store-built Docker image."""
    from app.factory.build.gates import GateResult

    script = ctx.workspace / ACCEPTANCE_SCRIPT_REL
    if not script.is_file():
        report = missing_acceptance_report(detail="scripts/acceptance.py is not stamped")
        write_acceptance_report(ctx.workspace, report)
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="STORE (acceptance): scripts/acceptance.py is missing",
            findings=["missing scripts/acceptance.py"],
            payload=report.to_json(),
        )

    if not _docker_available(ctx):
        report = missing_acceptance_report(
            detail="docker is required — Store-green runs acceptance inside the image"
        )
        write_acceptance_report(ctx.workspace, report)
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="STORE (acceptance): docker is not available; will not pass on a host-side skip",
            findings=["docker CLI missing — Store-green is image-measured"],
            payload=report.to_json(),
        )

    tag = "store-accept-" + hashlib.sha256(str(ctx.workspace.resolve()).encode()).hexdigest()[:12]
    name = tag + "-ctr"
    findings: List[str] = []

    def _run(argv: List[str]) -> Any:
        return ctx.run(argv)

    try:
        built = _run(["docker", "build", "-t", tag, "."])
        if getattr(built, "returncode", 1) != 0:
            err = ((getattr(built, "stderr", "") or "") + (getattr(built, "stdout", "") or "")).strip()
            findings.append((err or "docker build failed")[:400])
            report = missing_acceptance_report(detail="docker build failed")
            write_acceptance_report(ctx.workspace, report)
            return GateResult(
                ok=False,
                gate=GATE_NAME,
                detail="STORE (acceptance): docker build failed",
                findings=findings[:8],
                payload=report.to_json(),
            )
        started = _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-e",
                "PLATFORM_TOKEN=dev-local-token",
                tag,
            ]
        )
        if getattr(started, "returncode", 1) != 0:
            report = missing_acceptance_report(detail="docker run failed")
            write_acceptance_report(ctx.workspace, report)
            return GateResult(
                ok=False,
                gate=GATE_NAME,
                detail="STORE (acceptance): docker run failed",
                findings=[(getattr(started, "stderr", "") or "docker run failed")[:400]],
                payload=report.to_json(),
            )
        health_code = "000"
        probe = (
            "import urllib.request, time, sys\n"
            "for _ in range(30):\n"
            "    try:\n"
            "        r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)\n"
            "        print(r.status)\n"
            "        sys.exit(0)\n"
            "    except Exception:\n"
            "        time.sleep(1)\n"
            "print('000')\n"
            "sys.exit(1)\n"
        )
        for _attempt in range(8):
            probed = _run(["docker", "exec", name, "python3", "-c", probe])
            out = ((getattr(probed, "stdout", "") or "") + "\n" + (getattr(probed, "stderr", "") or ""))
            match = re.search(r"\b(200|503|000)\b", out)
            if match:
                health_code = match.group(1)
            if health_code == "200" or getattr(probed, "returncode", 1) == 0 and "200" in out:
                health_code = "200"
                break
            time.sleep(0.05)
        ran = _run(
            [
                "docker",
                "exec",
                "-e",
                "STORE_DOCKER_HEALTH=" + health_code,
                "-e",
                "PLATFORM_TOKEN=dev-local-token",
                name,
                "python3",
                "scripts/acceptance.py",
            ]
        )
        text = (getattr(ran, "stdout", "") or "") + "\n" + (getattr(ran, "stderr", "") or "")
        report = parse_acceptance_output(text)
        report.via = "docker exec python3 scripts/acceptance.py"
        write_acceptance_report(ctx.workspace, report)
        if not acceptance_is_kk(report):
            failed = [
                f"{line.name}={line.status}: {line.detail}"
                for line in report.lines
                if not line.satisfied
            ]
            return GateResult(
                ok=False,
                gate=GATE_NAME,
                detail="STORE (acceptance): %s/%s inside the Store-built image" % (report.passed, report.total),
                findings=failed[:12] or ["acceptance is not k/k"],
                payload=report.to_json(),
            )
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="STORE (acceptance): %s/%s inside the Store-built image" % (report.passed, report.total),
            payload=report.to_json(),
        )
    finally:
        _run(["docker", "rm", "-f", name])
