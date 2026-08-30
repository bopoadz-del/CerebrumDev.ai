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
from app.factory.build.writer_behaviour import (
    BEHAVIOUR_PROBE,
    F1_HALT,
    GATE_NAME,
    SCHEMA_HALT,
    banner_detail,
)

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
    assert result.detail == F1_HALT, result.detail
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


# A coder-shaped handler that ignores execute() errors — the live
# invoice-management shape (moonshot/kimi, 2026-08-30).
_DISHONEST_HANDLER = """    res = execute('database', {'input': payload}, action='insert')
    return {'ok': True, 'capability': CAPABILITY_ID, 'ignored': res}
"""


def _write_mixed_workspace(root: Path) -> None:
    """One honest capability and one that reports success over a failed block.

    The live Floor run had six capabilities; writer_behaviour failed the
    whole WRITER phase because one route lied. Isolated F1 is a miss.
    """
    _write_workspace(root, _GUARDED)

    app = root / "app"
    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            "MODELS = {'widget_intake': Widget, 'invoice_management': Widget}\n",
        ),
        encoding="utf-8",
    )
    jobs = (app / "jobs.py").read_text(encoding="utf-8")
    (app / "jobs.py").write_text(
        jobs.replace(
            "CAPABILITIES = [{'id': 'widget_intake', 'entity': 'widget'}]\n",
            "CAPABILITIES = ["
            "{'id': 'widget_intake', 'entity': 'widget'}, "
            "{'id': 'invoice_management', 'entity': 'invoice'}]\n",
        ),
        encoding="utf-8",
    )
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import widget_intake  # noqa: F401\n"
        "from app.actions import invoice_management  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "actions" / "invoice_management.py").write_text(
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'invoice_management'\n\n"
        "def handle(payload):\n"
        + _DISHONEST_HANDLER,
        encoding="utf-8",
    )
    routes = (app / "routes.py").read_text(encoding="utf-8")
    extra = '''
from app.actions import invoice_management as _invoice_action

@router.post("/invoice_management")
def invoice_management_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    CAPABILITY_ID = "invoice_management"
    handle = _invoice_action.handle
    save = lambda record: store.save("invoice", record)
    result = handle(payload)
    saved = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}
'''
    (app / "routes.py").write_text(routes + extra, encoding="utf-8")


def test_gate_records_an_isolated_success_over_failed_block_as_a_miss(tmp_path):
    """Regression for the live halt: one F1 must not fail the WRITER gate.

    A workspace with one honest capability and one that reports success
    over a failed block used to exit writer_behaviour non-zero and stop
    the run before TESTER/STORE_MANAGER (no zip). Isolated F1 is a miss.
    """
    _write_mixed_workspace(tmp_path)
    result = _run_gate(tmp_path)

    assert result.ok is True, result
    joined = " ".join(result.findings) + " " + " ".join(result.payload.get("misses") or [])
    assert "invoice_management" in joined, (result.detail, joined)
    assert "did not fail closed" in joined or "persisted" in joined, joined
    assert "miss" in result.detail
    assert "widget_intake" not in joined


def test_kernel_route_does_not_report_success_over_a_failed_block(tmp_path):
    """Live path: templated route -> run_capability -> dishonest handle().

    Isolated subprocess so the workspace's ``app`` package does not collide
    with the factory host. The coder handler returns ok:True after
    execute() errors; the kernel bridge must refuse ActionStatus.SUCCESS
    so the route cannot persist or answer ok:True.
    """
    import shutil

    from app.factory.build.roles import _render_kernel_bridge, _templated_route_body

    app = tmp_path / "app"
    (app / "actions").mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import invoice_management  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        "    return {'status': 'error', 'block': block_id,\n"
        "            'error': 'writer_behaviour gate: forced block failure'}\n",
        encoding="utf-8",
    )
    (app / "actions" / "invoice_management.py").write_text(
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'invoice_management'\n\n"
        "def handle(payload):\n"
        + _DISHONEST_HANDLER,
        encoding="utf-8",
    )
    (app / "kernel_bridge.py").write_text(_render_kernel_bridge(), encoding="utf-8")
    kernel_src = Path(__file__).resolve().parents[2] / "app" / "cerebrum_product_kernel"
    shutil.copytree(kernel_src, app / "cerebrum_product_kernel")

    route_body = _templated_route_body({"fields": []})
    (tmp_path / "probe.py").write_text(
        "import asyncio\n"
        "from app.kernel_bridge import run_capability\n"
        "\n"
        "saved = []\n"
        "\n"
        "async def _route(payload, run_capability, save, CAPABILITY_ID='invoice_management'):\n"
        f"{route_body}\n"
        "\n"
        "out = asyncio.run(_route({'reference': 'INV-1'}, run_capability, saved.append))\n"
        "print(out)\n"
        "print('SAVED', saved)\n"
        "assert out.get('ok') is False, out\n"
        "assert saved == [], out\n"
        "assert out.get('result', {}).get('status') != 'success', out\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "probe.py"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHONPATH": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_probe_emits_canonical_schema_halt():
    """The probe sentence and the host banner constant must stay one string."""
    assert SCHEMA_HALT in BEHAVIOUR_PROBE
    assert "GATE-FINDING: " + SCHEMA_HALT in BEHAVIOUR_PROBE


def test_banner_detail_schema_does_not_masquerade_as_f1():
    """Live construction: API findings were schema, Floor banner said F1."""
    assert banner_detail([SCHEMA_HALT]) == SCHEMA_HALT
    assert banner_detail(
        [SCHEMA_HALT, "site_diary_capture: refused a payload built from its own declared constraints (x)"]
    ) == SCHEMA_HALT
    assert F1_HALT not in banner_detail([SCHEMA_HALT])
    assert banner_detail(
        ["widget_intake: did not fail closed — answered {} while every block call failed (F1)"]
    ) == F1_HALT
    assert banner_detail([]) == "behaviour probe failed with no output"
    assert banner_detail(["workspace does not import: ModuleNotFoundError: x"]) == (
        "workspace does not import: ModuleNotFoundError: x"
    )


def _add_schema_refuser(root: Path, cap_id: str = "broken_schema") -> None:
    """A capability that refuses its own payload and never calls a block."""
    app = root / "app"
    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            f"MODELS = {{'widget_intake': Widget, '{cap_id}': Widget}}\n",
        )
        if "MODELS = {'widget_intake': Widget}\n" in models
        else models.replace(
            "MODELS = {'widget_intake': Widget, 'invoice_management': Widget}\n",
            "MODELS = {'widget_intake': Widget, "
            f"'invoice_management': Widget, '{cap_id}': Widget}}\n",
        ),
        encoding="utf-8",
    )
    jobs = (app / "jobs.py").read_text(encoding="utf-8")
    (app / "jobs.py").write_text(
        jobs.replace(
            "{'id': 'widget_intake', 'entity': 'widget'}",
            "{'id': 'widget_intake', 'entity': 'widget'}, "
            f"{{'id': '{cap_id}', 'entity': '{cap_id}'}}",
        ),
        encoding="utf-8",
    )
    init = (app / "actions" / "__init__.py").read_text(encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        init + f"from app.actions import {cap_id}  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "actions" / f"{cap_id}.py").write_text(
        f"CAPABILITY_ID = '{cap_id}'\n\n"
        "def handle(payload):\n"
        "    return {'ok': False, 'error': 'name is required'}\n",
        encoding="utf-8",
    )
    routes = (app / "routes.py").read_text(encoding="utf-8")
    extra = f'''
from app.actions import {cap_id} as _{cap_id}_action

@router.post("/{cap_id}")
def {cap_id}_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _{cap_id}_action.handle(payload)
'''
    (app / "routes.py").write_text(routes + extra, encoding="utf-8")


def test_gate_records_isolated_schema_refusal_as_a_miss(tmp_path):
    """Regression: schema miss must not halt a mixed workspace or look like F1.

    Live construction (sess_c6aa774686894c5a): findings were
    ``no capability accepted its own schema`` but the Floor banner said
    success-over-failed-block and WRITER SystemExit'd before TESTER.
    Isolated schema refusal next to an honest capability is a miss.
    """
    _write_workspace(tmp_path, _GUARDED)
    _add_schema_refuser(tmp_path)
    result = _run_gate(tmp_path)

    assert result.ok is True, result
    joined = " ".join(result.findings) + " " + result.detail
    assert "broken_schema" in joined, (result.detail, result.findings)
    assert "refused a payload" in joined or "schema" in result.detail, joined
    assert result.detail != F1_HALT
    assert F1_HALT not in result.detail
    assert "success over a failed block" not in result.detail
    assert SCHEMA_HALT not in result.findings
    payload = result.payload or {}
    assert any("broken_schema" in m for m in (payload.get("schema_misses") or payload.get("misses") or []))


def test_gate_fails_all_schema_with_schema_detail_not_f1(tmp_path):
    """Every capability refused its own schema: fail, but say schema not F1."""
    _write_workspace(tmp_path, _GUARDED)
    # Replace the honest route with a schema refuser so the workspace
    # has only capabilities that never accept their own payload.
    app = tmp_path / "app"
    (app / "actions" / "widget_intake.py").write_text(
        "CAPABILITY_ID = 'widget_intake'\n\n"
        "def handle(payload):\n"
        "    return {'ok': False, 'error': 'name is required'}\n",
        encoding="utf-8",
    )
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app.actions import widget_intake as _action\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/widget_intake")\n'
        "def widget_intake_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        "    return _action.handle(payload)\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.detail == SCHEMA_HALT, result.detail
    assert SCHEMA_HALT in result.findings
    assert result.detail != F1_HALT
    assert "success over a failed block" not in result.detail


def test_gate_does_not_halt_when_baseline_ok_false_because_block_failed(tmp_path):
    """Kernel wrap (#237): real blocks fail on the sample payload → ok:False.

    That used to empty ``targets`` and SystemExit with the schema sentence,
    which the host then labelled F1. A block was reached, so phase two
    must still run. An honest fail-closed handler passes.
    """
    _write_workspace(tmp_path, _GUARDED)
    app = tmp_path / "app"
    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        "    return {'status': 'error', 'block': block_id,\n"
        "            'error': 'sample payload rejected by block'}\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)

    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != F1_HALT
    assert SCHEMA_HALT not in (result.findings or [])
    assert result.detail != SCHEMA_HALT


def test_kernel_templated_route_with_failing_blocks_does_not_halt_as_schema(tmp_path):
    """Live path: kernel execute_action template + blocks that reject sample.

    last_event on the construction run was ``wrote route … (kernel
    execute_action template)``. After #237 the wrap answers ok:False when
    a block fails, which the schema phase treated as 'did not accept' and
    halted the whole WRITER. The miss parser never saw phase two.
    """
    import shutil

    from app.factory.build.roles import _render_kernel_bridge, _templated_route_body

    _write_workspace(tmp_path, _GUARDED)
    app = tmp_path / "app"
    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        "    return {'status': 'error', 'block': block_id,\n"
        "            'error': 'sample payload rejected by block'}\n",
        encoding="utf-8",
    )
    (app / "kernel_bridge.py").write_text(_render_kernel_bridge(), encoding="utf-8")
    kernel_src = Path(__file__).resolve().parents[2] / "app" / "cerebrum_product_kernel"
    shutil.copytree(kernel_src, app / "cerebrum_product_kernel")

    route_body = _templated_route_body({"fields": []})
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        "from app.kernel_bridge import run_capability\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/widget_intake")\n'
        "async def widget_intake_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        '    CAPABILITY_ID = "widget_intake"\n'
        "    save = lambda record: store.save(\"widget\", record)\n"
        f"{route_body}\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)

    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != F1_HALT
    assert result.detail != SCHEMA_HALT
    assert SCHEMA_HALT not in (result.findings or [])
