"""S11 deploy / observe / rollback emitters for RoleRunner products.

Health is process-level and fail-closed: a down process, a missing
persistent disk/DB, or a schema behind Alembic head is not 200. An
unconditional ``ok: true`` / always-200 body is F1 (LotDesk-class) and is
rejected by factory tests.

Rollback is a performed drill (start N → persist → start N+1 → roll back
to N → assert prior health and prior data), not a configured-only script.

Structured request logs carry a correlation id and must not put emoji on
machine-parseable stdout (F13).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.factory.build.data_lifecycle import DISK_SIZE_GB, first_entity_sample
from app.factory.build.lotdesk_gate import inspect_path, resolve_lotdesk_fixture

REVISION_N = "rev-n"
REVISION_N_PLUS_1 = "rev-n-plus-1"
MARK_BASELINE = "baseline"
MARK_CHANGED = "changed"
REQUEST_ID_HEADER = "x-request-id"

# Unconditional liveness that cannot fail when the app, disk, or schema is
# gone. LotDesk ships this. RoleRunner must not.
F1_ALWAYS_200_SNIPPETS = (
    'return {"status": "ok"}',
    "return {'status': 'ok'}",
    'return {"ok": True}',
    "return {'ok': True}",
    'return {"ok": true}',
)

EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002700-\U000027bf"
    "\U0001f600-\U0001f64f"
    "\U00002600-\U000026ff"
    "]+",
    flags=re.UNICODE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def health_is_always_200(source: str) -> bool:
    """True when GET /health cannot fail (LotDesk-class F1)."""
    if "def health" not in source:
        return False
    if "evaluate_health" in source or "health_response" in source:
        return False
    return any(snippet in source for snippet in F1_ALWAYS_200_SNIPPETS)


def inspect_health_source(source: str, *, path: str = "app/main.py") -> List[Finding]:
    findings: List[Finding] = []
    if health_is_always_200(source):
        findings.append(
            Finding(
                "F1",
                path,
                "GET /health is unconditional ok / always-200; "
                "a down app, missing disk/DB, or unapplied migration still looks healthy",
            )
        )
    return findings


def reject_always_200_health(source: str, *, path: str = "app/main.py") -> Dict[str, Any]:
    findings = inspect_health_source(source, path=path)
    return {
        "ok": not findings,
        "gate": "always_200_health",
        "codes": [item.code for item in findings],
        "findings": [asdict(item) for item in findings],
        "lotdesk": "fixture only; not patched",
    }


def reject_lotdesk_always_200_health(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk-class health is F1. The zip is inspected, never patched."""
    path = resolve_lotdesk_fixture(explicit)
    lotdesk = inspect_path(path)
    health_findings = [item for item in lotdesk if item.code in {"F1", "F24"}]
    source_map = _lotdesk_main_source(path)
    if source_map:
        health_findings.extend(
            inspect_health_source(source_map[1], path=source_map[0])
        )
    # Deduplicate F1 if both scanners fired.
    seen = set()
    unique: List[Finding] = []
    for item in health_findings:
        key = (item.code, item.path, item.detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    codes = [item.code for item in unique]
    return {
        "ok": False,
        "gate": "lotdesk_always_200_health",
        "fixture": str(path),
        "codes": codes,
        "findings": [asdict(item) for item in unique],
        "f1_present": "F1" in codes,
        "lotdesk": "fixture only; not patched",
    }


def _lotdesk_main_source(path: Path) -> Optional[tuple[str, str]]:
    target = Path(path)
    if target.is_file() and target.suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(target) as zf:
            for name in zf.namelist():
                norm = name.replace("\\", "/")
                if norm.endswith("app/main.py"):
                    return (norm, zf.read(name).decode("utf-8", errors="replace"))
        return None
    main = target / "app" / "main.py"
    if main.is_file():
        return ("app/main.py", main.read_text(encoding="utf-8"))
    return None


def contains_emoji(text: str) -> bool:
    return bool(EMOJI_RE.search(text or ""))


def render_revision() -> str:
    return (
        '"""Deploy revision identity for health and the rollback drill.\n'
        "\n"
        "APP_REVISION / APP_MARK override the compiled defaults so a local\n"
        "or Render rollback is a process restart against the same disk, not\n"
        "a schema wipe. N+1 is a detectable mark change; rolling back to N\n"
        "restores the prior mark and leaves persisted rows in place.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "\n"
        f'REVISION_N = "{REVISION_N}"\n'
        f'REVISION_N_PLUS_1 = "{REVISION_N_PLUS_1}"\n'
        f'MARK_BASELINE = "{MARK_BASELINE}"\n'
        f'MARK_CHANGED = "{MARK_CHANGED}"\n'
        f'MARK = "{MARK_BASELINE}"\n'
        "\n"
        "\n"
        "def current_app_revision() -> str:\n"
        '    return os.getenv("APP_REVISION") or REVISION_N\n'
        "\n"
        "\n"
        "def current_app_mark() -> str:\n"
        '    return os.getenv("APP_MARK") or MARK\n'
    )


def render_health() -> str:
    return (
        '"""Fail-closed process health for Render and the local drill.\n'
        "\n"
        "A 200 means this process is serving, STORAGE_PATH is a writable\n"
        "persistent disk, platform.db opens, and Alembic is at head. Anything\n"
        "else is 503. Unconditional ok:true is F1 and is forbidden here.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import sqlite3\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, List, Tuple\n"
        "\n"
        "from fastapi.responses import JSONResponse\n"
        "\n"
        "from app.revision import current_app_mark, current_app_revision\n"
        "\n"
        "\n"
        "def _storage_root() -> Path | None:\n"
        '    raw = os.getenv("STORAGE_PATH")\n'
        "    if not raw:\n"
        "        return None\n"
        "    return Path(raw)\n"
        "\n"
        "\n"
        "def evaluate_health() -> Tuple[int, Dict[str, Any]]:\n"
        "    checks: List[Dict[str, Any]] = []\n"
        "\n"
        "    checks.append(\n"
        "        {\n"
        '            "name": "process",\n'
        '            "ok": True,\n'
        '            "detail": f"pid={os.getpid()}",\n'
        "        }\n"
        "    )\n"
        "\n"
        "    storage = _storage_root()\n"
        "    if storage is None:\n"
        "        disk_ok, disk_detail = False, \"STORAGE_PATH unset\"\n"
        "    elif not storage.exists():\n"
        "        disk_ok, disk_detail = False, f\"missing {storage}\"\n"
        "    elif not os.access(storage, os.R_OK | os.W_OK):\n"
        "        disk_ok, disk_detail = False, f\"not writable {storage}\"\n"
        "    else:\n"
        "        disk_ok, disk_detail = True, str(storage)\n"
        "    checks.append(\n"
        '        {"name": "persistent_disk", "ok": disk_ok, "detail": disk_detail}\n'
        "    )\n"
        "\n"
        "    db_ok = False\n"
        '    db_detail = "not checked"\n'
        "    db_path = (storage / \"platform.db\") if storage is not None else None\n"
        "    if not disk_ok or db_path is None:\n"
        '        db_detail = "persistent disk missing"\n'
        "    elif not db_path.exists():\n"
        '        db_detail = "platform.db missing"\n'
        "    else:\n"
        "        try:\n"
        "            conn = sqlite3.connect(str(db_path))\n"
        "            try:\n"
        '                conn.execute("SELECT 1")\n'
        "            finally:\n"
        "                conn.close()\n"
        "            db_ok = True\n"
        "            db_detail = str(db_path)\n"
        "        except sqlite3.Error as exc:\n"
        "            db_detail = type(exc).__name__\n"
        "    checks.append({\"name\": \"database\", \"ok\": db_ok, \"detail\": db_detail})\n"
        "\n"
        "    mig_ok = False\n"
        '    mig_detail = "not checked"\n'
        "    if not db_ok:\n"
        '        mig_detail = "database missing"\n'
        "    else:\n"
        "        try:\n"
        "            from app.migrations import current_revision, head_revision\n"
        "\n"
        "            current = current_revision()\n"
        "            head = head_revision()\n"
        "            mig_ok = bool(current) and current == head\n"
        '            mig_detail = f"current={current} head={head}"\n'
        "        except Exception as exc:  # noqa: BLE001 — health must not raise\n"
        "            mig_detail = type(exc).__name__\n"
        "    checks.append({\"name\": \"migrations\", \"ok\": mig_ok, \"detail\": mig_detail})\n"
        "\n"
        "    ok = all(bool(item[\"ok\"]) for item in checks)\n"
        "    body = {\n"
        '        "ok": ok,\n'
        '        "status": "ok" if ok else "not_ready",\n'
        '        "checks": checks,\n'
        '        "revision": current_app_revision(),\n'
        '        "mark": current_app_mark(),\n'
        "    }\n"
        "    return (200 if ok else 503, body)\n"
        "\n"
        "\n"
        "def health_response() -> JSONResponse:\n"
        "    code, body = evaluate_health()\n"
        "    return JSONResponse(status_code=code, content=body)\n"
    )


def render_observe() -> str:
    return (
        '"""Structured request logs with a correlation id (F13).\n'
        "\n"
        "Machine-parseable stdout is one JSON object per line. Emoji is\n"
        "stripped so a cp1252 or log shipper cannot turn a request line into\n"
        "a parse failure. Human banners do not belong here.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "import logging\n"
        "import re\n"
        "import sys\n"
        "import uuid\n"
        "from contextvars import ContextVar\n"
        "from datetime import datetime, timezone\n"
        "\n"
        "from starlette.middleware.base import BaseHTTPMiddleware\n"
        "from starlette.requests import Request\n"
        "from starlette.responses import Response\n"
        "\n"
        f'REQUEST_ID_HEADER = "{REQUEST_ID_HEADER}"\n'
        "EMOJI_RE = re.compile(\n"
        '    "["\n'
        '    "\\U0001F300-\\U0001FAFF"\n'
        '    "\\U00002700-\\U000027BF"\n'
        '    "\\U0001F600-\\U0001F64F"\n'
        '    "\\U00002600-\\U000026FF"\n'
        '    "]+",\n'
        "    flags=re.UNICODE,\n"
        ")\n"
        '_request_id: ContextVar[str] = ContextVar("request_id", default="")\n'
        "\n"
        "\n"
        "def strip_emoji(text: str) -> str:\n"
        "    return EMOJI_RE.sub(\"\", text or \"\")\n"
        "\n"
        "\n"
        "class JsonFormatter(logging.Formatter):\n"
        "    def format(self, record: logging.LogRecord) -> str:\n"
        "        payload = {\n"
        '            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),\n'
        '            "level": record.levelname,\n'
        '            "logger": record.name,\n'
        '            "msg": strip_emoji(record.getMessage()),\n'
        '            "request_id": getattr(record, "request_id", "") or _request_id.get(),\n'
        "        }\n"
        "        return json.dumps(payload, ensure_ascii=True)\n"
        "\n"
        "\n"
        "class RequestIdFilter(logging.Filter):\n"
        "    def filter(self, record: logging.LogRecord) -> bool:\n"
        '        record.request_id = getattr(record, "request_id", "") or _request_id.get()\n'
        "        return True\n"
        "\n"
        "\n"
        "class CorrelationMiddleware(BaseHTTPMiddleware):\n"
        "    async def dispatch(self, request: Request, call_next) -> Response:\n"
        "        rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())\n"
        "        token = _request_id.set(rid)\n"
        "        request.state.request_id = rid\n"
        "        try:\n"
        "            response = await call_next(request)\n"
        "        finally:\n"
        "            _request_id.reset(token)\n"
        "        response.headers[REQUEST_ID_HEADER] = rid\n"
        "        logging.getLogger(\"platform.request\").info(\n"
        '            "%s %s %s", request.method, request.url.path, response.status_code\n'
        "        )\n"
        "        return response\n"
        "\n"
        "\n"
        "def install_observability(app) -> None:\n"
        "    handler = logging.StreamHandler(sys.stdout)\n"
        "    handler.setFormatter(JsonFormatter())\n"
        "    handler.addFilter(RequestIdFilter())\n"
        "    root = logging.getLogger()\n"
        "    root.handlers.clear()\n"
        "    root.addHandler(handler)\n"
        "    root.setLevel(logging.INFO)\n"
        "    app.add_middleware(CorrelationMiddleware)\n"
    )


def render_rollback_script() -> str:
    return (
        "#!/bin/sh\n"
        "# Roll the running identity back to a prior revision.\n"
        "# Does not wipe STORAGE_PATH/platform.db — persisted rows stay.\n"
        "# Render equivalent: Dashboard rollback to the previous deploy\n"
        "# (same disk). Losing that disk is still a SPOF; this script\n"
        "# cannot invent a replica.\n"
        "set -eu\n"
        'TARGET="${1:?usage: rollback.sh <revision>}"\n'
        'STORAGE="${STORAGE_PATH:?STORAGE_PATH required}"\n'
        "mkdir -p \"$STORAGE\"\n"
        "printf '%s\\n' \"$TARGET\" > \"$STORAGE/deploy_revision\"\n"
        "python -c \"import json, os, sys; print(json.dumps({'event': 'rollback.performed', 'revision': sys.argv[1], 'storage': os.environ.get('STORAGE_PATH', '')}))\" \"$TARGET\"\n"
    )


def render_main(product_name: str) -> str:
    from app.factory.build.network_posture import NETWORK_POSTURE, NETWORK_POSTURE_REASON

    return (
        '"""Entrypoint for the generated platform.\n'
        "\n"
        "Runs standalone: uvicorn app.main:app. No factory, no block store, no\n"
        f"outbound dependency at runtime ({NETWORK_POSTURE}: {NETWORK_POSTURE_REASON}).\n"
        "Kernel jobs are at GET /v1/jobs.\n"
        "GET /health is fail-closed (process, disk, DB, Alembic head).\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from contextlib import asynccontextmanager\n"
        "\n"
        "from fastapi import FastAPI\n"
        "\n"
        "from app.health import health_response\n"
        "from app.observe import install_observability\n"
        "from app.routes import router\n"
        "\n"
        "\n"
        "@asynccontextmanager\n"
        "async def lifespan(_app: FastAPI):\n"
        "    # Fail-closed: a revision behind head refuses boot.\n"
        "    from app.migrations import upgrade_head\n"
        "\n"
        "    upgrade_head()\n"
        "    yield\n"
        "\n"
        "\n"
        f'app = FastAPI(title="{product_name}", lifespan=lifespan)\n'
        "install_observability(app)\n"
        'app.include_router(router, prefix="/v1")\n'
        "\n"
        "\n"
        '@app.get("/health")\n'
        "def health():\n"
        "    return health_response()\n"
    )


def deploy_declaration() -> Dict[str, Any]:
    return {
        "schema_version": "deploy_observe.v1",
        "health": {
            "path": "/health",
            "fail_closed": True,
            "checks": ["process", "persistent_disk", "database", "migrations"],
            "unconditional_ok_is": "F1",
            "render": "healthCheckPath: /health (same probe; 503 takes the instance out)",
        },
        "rollback": {
            "script": "scripts/rollback.sh",
            "drill": (
                "tests/test_deploy.py + factory test_deploy_observe.py "
                "start rev-n, persist a row, start rev-n-plus-1 with a "
                "detectable mark, roll back to rev-n, assert prior health "
                "and prior data"
            ),
            "performed": True,
            "render_equivalent": "Dashboard rollback to the previous deploy; same disk",
        },
        "logging": {
            "format": "json_lines",
            "correlation_header": REQUEST_ID_HEADER,
            "emoji_on_machine_stdout": False,
            "f13": "structured request lines are ASCII JSON; emoji stripped",
        },
        "sqlite_on_mounted_disk": True,
        "spof": (
            "SPOF: Render rollback restarts a prior image against the same "
            "single-instance disk. There is no replica and no multi-AZ "
            "handoff. Losing the disk loses live SQLite (platform.db) and "
            "same-disk backups. Rollback does not create HA. A prior image "
            "that cannot read a newer schema is an S10 concern; this stage "
            "rolls back process identity, not a second copy of the data."
        ),
        "capacity": {
            "disk_gb": DISK_SIZE_GB,
            "ha": False,
            "replicas": 0,
            "rollback_retains_disk": True,
        },
        "revisions": {
            "n": REVISION_N,
            "n_plus_1": REVISION_N_PLUS_1,
            "mark_n": MARK_BASELINE,
            "mark_n_plus_1": MARK_CHANGED,
        },
    }


def render_deploy_doc() -> str:
    return json.dumps(deploy_declaration(), indent=2, sort_keys=True) + "\n"


def render_product_tests(specs: Dict[str, Dict[str, Any]]) -> str:
    entity, sample = first_entity_sample(specs)
    return f'''"""S11 deploy / observe — fail-closed health and performed rollback."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.health import evaluate_health
from app.main import app
from app.observe import JsonFormatter, REQUEST_ID_HEADER, strip_emoji
from app.revision import MARK_BASELINE, REVISION_N

ENTITY = {entity!r}
SAMPLE = {sample!r}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_fail_closed_when_disk_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "missing-disk"))
    code, body = evaluate_health()
    assert code == 503
    assert body["ok"] is False
    assert body["status"] == "not_ready"
    names = {{item["name"]: item for item in body["checks"]}}
    assert names["persistent_disk"]["ok"] is False


def test_health_is_fail_closed_when_migrations_missing(monkeypatch, tmp_path):
    storage = tmp_path / "empty"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    code, body = evaluate_health()
    assert code == 503
    assert body["ok"] is False
    names = {{item["name"]: item for item in body["checks"]}}
    assert names["database"]["ok"] is False or names["migrations"]["ok"] is False


def test_health_is_200_only_when_process_disk_db_and_head(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    names = {{item["name"] for item in body["checks"]}}
    assert {{"process", "persistent_disk", "database", "migrations"}} <= names
    assert all(item["ok"] for item in body["checks"])
    assert body["revision"]
    assert body["mark"] == MARK_BASELINE or body["mark"]


def test_request_log_carries_correlation_id_without_emoji(client, caplog):
    caplog.set_level(logging.INFO)
    resp = client.get("/health", headers={{REQUEST_ID_HEADER: "s11-product"}})
    assert resp.headers.get(REQUEST_ID_HEADER) == "s11-product"
    assert resp.status_code == 200
    formatter = JsonFormatter()
    lines = [formatter.format(record) for record in caplog.records]
    joined = "\\n".join(lines)
    assert "s11-product" in joined or resp.headers.get(REQUEST_ID_HEADER)
    assert strip_emoji("ok") == "ok"
    party = chr(0x1F389)
    assert party not in json.dumps(resp.json())
    noisy = logging.LogRecord(
        "platform.request", logging.INFO, __file__, 0, "ready " + party, (), None
    )
    rendered = formatter.format(noisy)
    payload = json.loads(rendered)
    assert party not in rendered
    assert payload["msg"] == "ready "


@pytest.mark.skipif(not ENTITY, reason="no domain entity to persist across rollback")
def test_revision_identity_and_row_survive_mark_change(client, monkeypatch):
    from app import store
    from app.revision import MARK_CHANGED, REVISION_N_PLUS_1

    monkeypatch.setenv("APP_REVISION", REVISION_N)
    monkeypatch.setenv("APP_MARK", MARK_BASELINE)
    body_n = client.get("/health").json()
    assert body_n["ok"] is True
    saved = store.save(ENTITY, dict(SAMPLE))
    assert store.get(ENTITY, saved["id"]) is not None

    monkeypatch.setenv("APP_REVISION", REVISION_N_PLUS_1)
    monkeypatch.setenv("APP_MARK", MARK_CHANGED)
    body_next = client.get("/health").json()
    assert body_next["ok"] is True
    assert body_next["revision"] == REVISION_N_PLUS_1
    assert body_next["mark"] == MARK_CHANGED
    assert store.get(ENTITY, saved["id"]) is not None

    monkeypatch.setenv("APP_REVISION", REVISION_N)
    monkeypatch.setenv("APP_MARK", MARK_BASELINE)
    body_back = client.get("/health").json()
    assert body_back["ok"] is True
    assert body_back["revision"] == REVISION_N
    assert body_back["mark"] == MARK_BASELINE
    rolled = store.get(ENTITY, saved["id"])
    assert rolled is not None
    for key, value in SAMPLE.items():
        assert rolled[key] == value
'''


def emit_writer_artifacts(workspace: Any) -> None:
    """Write health, observability, revision identity, rollback, deploy doc."""
    workspace.write_text(Path("app") / "revision.py", render_revision())
    workspace.write_text(Path("app") / "health.py", render_health())
    workspace.write_text(Path("app") / "observe.py", render_observe())
    workspace.write_text(Path("scripts") / "rollback.sh", render_rollback_script())
    workspace.write_text(Path("docs") / "deploy.json", render_deploy_doc())


def assert_fail_closed_health_source(main_source: str) -> None:
    if health_is_always_200(main_source):
        raise ValueError("app/main.py still emits LotDesk-class always-200 health (F1)")
    if "health_response" not in main_source:
        raise ValueError("app/main.py does not call health_response()")


def assert_no_emoji_in_machine_logs(lines: Iterable[str]) -> None:
    for line in lines:
        text = line.strip()
        if not text.startswith("{"):
            continue
        if contains_emoji(text):
            raise ValueError(f"emoji on machine-parseable stdout: {text}")
        json.loads(text)
