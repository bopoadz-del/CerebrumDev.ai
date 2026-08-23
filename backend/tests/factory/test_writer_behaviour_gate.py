"""The WRITER gate rejects a route that reports success over a failed block.

Failing-first by construction: the same workspace is probed twice, differing
only in whether the route checks its handler's result before persisting. A
gate that passed both would not be testing anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build.gates import GateContext, gate_writer_contract
from app.factory.build.authority import BuildRole
from app.factory.build.writer_behaviour import GATE_NAME

# A route body that persists whatever arrived, regardless of the handler.
# This is LotDesk's shape (app/routes.py:127-128 of the shipped artifact).
_UNGUARDED = """    result = handle(payload)
    saved = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}
"""

# The same route, checking the handler before it writes.
_GUARDED = """    result = handle(payload)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    saved = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}
"""


def _write_workspace(root: Path, route_body: str) -> None:
    app = root / "app"
    (app / "actions").mkdir(parents=True, exist_ok=True)

    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import widget_intake  # noqa: F401\n", encoding="utf-8"
    )

    (app / "dispatch.py").write_text(
        'def execute(block_id, payload=None, action=None, params=None):\n'
        '    return {"status": "ok", "block": block_id}\n',
        encoding="utf-8",
    )

    (app / "models.py").write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from typing import Optional\n\n"
        "@dataclass\n"
        "class Widget:\n"
        "    id: Optional[int] = None\n"
        "    name: str = ''\n"
        "    size: int = 1\n"
        "    status: str = 'new'\n"
        "    FIELDS = ['name', 'size', 'status']\n"
        "    CONSTRAINTS = {'size': {'min': 1, 'max': 99},\n"
        "                   'status': {'allowed_values': ['new', 'done']}}\n\n"
        "MODELS = {'widget_intake': Widget}\n",
        encoding="utf-8",
    )

    (app / "jobs.py").write_text(
        "CAPABILITIES = [{'id': 'widget_intake', 'entity': 'widget'}]\n"
        "JOBS = []\nCATALOG = {}\nGATES = {}\n",
        encoding="utf-8",
    )

    # Entity name deliberately differs from the capability id, so a gate that
    # guesses the entity finds nothing and its row assertion goes inert.
    (app / "store.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n\n"
        "def _f(entity):\n"
        "    root = Path(os.getenv('STORAGE_PATH', './data'))\n"
        "    root.mkdir(parents=True, exist_ok=True)\n"
        "    return root / (entity + '.json')\n\n"
        "def list_all(entity):\n"
        "    p = _f(entity)\n"
        "    return json.loads(p.read_text()) if p.is_file() else []\n\n"
        "def save(entity, record):\n"
        "    rows = list_all(entity)\n"
        "    rows.append(record)\n"
        "    _f(entity).write_text(json.dumps(rows))\n"
        "    return record\n",
        encoding="utf-8",
    )

    (app / "actions" / "widget_intake.py").write_text(
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'widget_intake'\n\n"
        "def handle(payload):\n"
        "    res = execute('database', {'input': payload}, action='insert')\n"
        "    if res.get('status') == 'error':\n"
        "        return {'ok': False, 'error': res.get('error')}\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n",
        encoding="utf-8",
    )

    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        "from app.actions import widget_intake as _action\n\n"
        "router = APIRouter()\n\n"
        "def _handle(payload):\n"
        "    return _action.handle(payload)\n\n"
        '@router.post("/widget_intake")\n'
        "def widget_intake_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        '    CAPABILITY_ID = "widget_intake"\n'
        "    handle = _handle\n"
        '    save = lambda record: store.save("widget", record)\n'
        f"{route_body}",
        encoding="utf-8",
    )

    (app / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from app.routes import router\n"
        'app = FastAPI(title="probe")\n'
        'app.include_router(router, prefix="/v1")\n\n'
        '@app.get("/health")\n'
        "def health():\n"
        '    return {"status": "ok"}\n',
        encoding="utf-8",
    )


def _run_gate(workspace: Path):
    def runner(argv, *, cwd, timeout):
        return subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )

    ctx = GateContext(workspace=workspace, role=BuildRole.WRITER, runner=runner)
    return gate_writer_contract(ctx)


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="probe uses modern typing in the fixture"
)
def test_gate_rejects_route_that_persists_over_a_failed_block(tmp_path):
    _write_workspace(tmp_path, _UNGUARDED)
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.gate == GATE_NAME
    joined = " ".join(result.findings)
    assert "did not fail closed" in joined, joined
    # The row assertion must actually fire, not go quiet because the entity
    # name was guessed from the capability id.
    assert "persisted" in joined, joined


def test_gate_passes_when_the_route_checks_its_handler(tmp_path):
    _write_workspace(tmp_path, _GUARDED)
    result = _run_gate(tmp_path)

    assert result.ok is True, result.findings


def test_gate_reports_a_writer_that_produced_nothing(tmp_path):
    result = _run_gate(tmp_path)

    assert result.ok is False
    assert "missing" in result.detail or "nothing" in result.detail
