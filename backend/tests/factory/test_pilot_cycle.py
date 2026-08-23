"""Pilot cycle must not patch vendor/** after CLONER.

F29: prepare_pilot_workspace used to regex-patch vendored adapters immediately
before the pilot gate. That is gone. CLONER emission writes the contracts.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import (
    SEALED_AFTER_CLONER,
    AuthorityError,
    BuildRole,
    assert_write_allowed,
)
from app.factory.build.offline_adapters import (
    AIOFILES_MARKER,
    ENSURE_READY_MARKER,
    MCP_OFFLINE_MARKER,
    QUERY_UNWIRED_MARKER,
    emit_database_insert,
    emit_database_query,
    emit_instantiate_ready,
    emit_notification_mcp,
    emit_storage_aiofiles,
)
from app.factory.build.roles import RoleContext, run_tester, run_writer
from app.factory.build.runner import RoleRunner
from app.factory.build.workspace import RoleWorkspace

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def test_runner_does_not_invoke_prepare_pilot_workspace():
    import app.factory.build.runner as runner_mod

    source = inspect.getsource(runner_mod)
    assert "prepare_pilot_workspace" not in source
    assert "pilot adapters patched" not in source


def test_prepare_pilot_workspace_is_gone():
    import app.factory.build.pilot as pilot_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")


def test_cloner_emission_contains_sqlite_init_contract():
    shim = (
        "def _instantiate_store_block(block_cls):\n"
        "    attempts = []\n"
        "    for call in (lambda: block_cls(),):\n"
        "        try:\n"
        "            return call()\n"
        "        except TypeError as exc:\n"
        "            attempts.append(exc)\n"
        "    raise attempts[-1]\n"
    )
    out = emit_instantiate_ready(shim)
    assert ENSURE_READY_MARKER in out
    assert "return _ensure_store_block_ready(call())" in out


def test_cloner_emission_contains_notification_mcp_contract():
    src = (
        "        try:\n"
        "            from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "            from app.dependencies import _create_block_instance\n"
        "            block = _create_block_instance(BLOCK_REGISTRY[block_name])\n"
    )
    out = emit_notification_mcp(src)
    assert MCP_OFFLINE_MARKER in out
    assert "offline" in out


def test_cloner_emission_contains_database_insert_and_query_contracts():
    insert = (
        "        except Exception as e:\n"
        '            return {"error": f"Insert failed: {str(e)}"}\n'
    )
    out = emit_database_insert(insert)
    assert "CREATE TABLE IF NOT EXISTS" in out

    query = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n"
    )
    out = emit_database_query(query)
    assert QUERY_UNWIRED_MARKER in out
    assert "SELECT * FROM" in out


def test_cloner_emission_contains_storage_aiofiles_contract():
    out = emit_storage_aiofiles("import aiofiles\n")
    assert AIOFILES_MARKER in out


def test_vendor_is_sealed_after_cloner(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "vendor" / "blocks" / "database").mkdir(parents=True)
    with pytest.raises(AuthorityError, match="sealed after CLONER"):
        assert_write_allowed(
            BuildRole.WRITER,
            ws / "vendor" / "blocks" / "database" / "block.py",
            workspace=ws,
            sealed=SEALED_AFTER_CLONER,
        )
    with pytest.raises(AuthorityError, match="FAILED_AUTHORITY|sealed after CLONER"):
        assert_write_allowed(
            BuildRole.TESTER,
            ws / "vendor" / "blocks" / "database" / "block.py",
            workspace=ws,
            sealed=SEALED_AFTER_CLONER,
        )


def test_opening_pilot_does_not_rewrite_vendor(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    out = tmp_path / "build"
    first = RoleRunner(load_blueprint(SMOKE), out).run()
    assert first.ok, first.to_dict()

    def _vendor_tree(root: Path) -> dict:
        tree = {}
        vendor = root / "vendor"
        if not vendor.is_dir():
            return tree
        for path in sorted(vendor.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            tree[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return tree

    before = _vendor_tree(out)
    assert before, "code-phase must have vendored blocks"
    second = RoleRunner(load_blueprint(SMOKE), out, cycle="pilot").run()
    after = _vendor_tree(out)
    assert before == after, "opening a pilot cycle must not rewrite vendor/**"
    assert "prepare_pilot_workspace" not in (out / "build_ledger.jsonl").read_text(
        encoding="utf-8"
    )
    assert "pilot adapters patched" not in (out / "build_ledger.jsonl").read_text(
        encoding="utf-8"
    )
    # Pilot may fail the Store-green gate on a mirror-only smoke product.
    # The F29 contract is: it must not patch vendor to get there.
    assert second.outcome.value != "FAILED_AUTHORITY"


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = ()


class _Plan:
    def __init__(self):
        self.capabilities = (_Cap("vehicle_inventory"),)


class _Blueprint:
    product_name = "LotDesk"
    product_id = "used-cars"
    vertical = "used_cars"


def test_pilot_writer_without_coder_keeps_existing_handlers(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "ws")
    handler = Path("app") / "actions" / "vehicle_inventory.py"
    ws.write_text(handler, "CAPABILITY_ID = 'vehicle_inventory'\n# kimi\n")
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        work_list=("vehicle_inventory rejected a payload",),
        state={"build_cycle": "pilot", "vendored_blocks": ("database",)},
    )
    result = run_writer(ctx)
    assert result.ok
    assert "no coder key" in result.detail
    assert ws.read_text(handler) == "CAPABILITY_ID = 'vehicle_inventory'\n# kimi\n"


def test_pilot_tester_keeps_existing_suite(tmp_path):
    ws = RoleWorkspace(BuildRole.TESTER, tmp_path / "ws")
    ws.write_text(Path("tests") / "test_smoke.py", "def test_ok():\n    assert True\n")
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        state={"build_cycle": "pilot", "vendored_blocks": ()},
    )
    result = run_tester(ctx)
    assert result.ok
    assert "existing suite kept" in result.detail
    assert "def test_ok" in ws.read_text(Path("tests") / "test_smoke.py")
