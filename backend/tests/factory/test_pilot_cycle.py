"""Resume-to-pilot must patch Store-unwired adapters without wiping WRITER files."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.pilot import prepare_pilot_workspace
from app.factory.build.roles import RoleContext, run_tester, run_writer
from app.factory.build.workspace import RoleWorkspace


def test_prepare_pilot_workspace_patches_adapters_not_handlers(tmp_path: Path):
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "vehicle_inventory.py").write_text(
        "CAPABILITY_ID = 'vehicle_inventory'\n", encoding="utf-8"
    )
    block = tmp_path / "vendor" / "blocks" / "database"
    block.mkdir(parents=True)
    (block / "block.py").write_text(
        "def _instantiate_store_block(block_cls):\n"
        "    attempts = []\n"
        "    for call in (lambda: block_cls(),):\n"
        "        try:\n"
        "            return call()\n"
        "        except TypeError as exc:\n"
        "            attempts.append(exc)\n"
        "    raise attempts[-1]\n",
        encoding="utf-8",
    )
    notify = tmp_path / "vendor" / "cerebrum" / "blocks"
    notify.mkdir(parents=True)
    (notify / "notification.py").write_text(
        "        try:\n"
        "            from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "            from app.dependencies import _create_block_instance\n"
        "            block = _create_block_instance(BLOCK_REGISTRY[block_name])\n",
        encoding="utf-8",
    )
    (notify / "database.py").write_text(
        "        except Exception as e:\n"
        '            return {"error": f"Insert failed: {str(e)}"}\n'
        "\n"
        "    async def _query(self, data: Dict) -> Dict:\n"
        '        """Execute SELECT query"""\n'
        '        sql = data.get("sql")\n'
        '        params = data.get("params", ())\n'
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n",
        encoding="utf-8",
    )
    (notify / "storage.py").write_text("import aiofiles\n", encoding="utf-8")

    touched = prepare_pilot_workspace(tmp_path)
    assert "vendor/blocks/database/block.py" in touched
    assert "vendor/cerebrum/blocks/notification.py" in touched
    assert "vendor/cerebrum/blocks/database.py" in touched
    assert "vendor/cerebrum/blocks/storage.py" in touched
    assert not any(p.startswith("app/") for p in touched)

    helper = (block / "block.py").read_text(encoding="utf-8")
    assert "_ensure_store_block_ready" in helper
    assert "return _ensure_store_block_ready(call())" in helper
    mcp = (notify / "notification.py").read_text(encoding="utf-8")
    assert "Store-unwired MCP" in mcp
    assert "offline" in mcp
    db = (notify / "database.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" in db
    assert "Store-unwired query" in db
    assert "SELECT * FROM {table}" in db
    storage = (notify / "storage.py").read_text(encoding="utf-8")
    assert "Store-unwired aiofiles" in storage
    assert (actions / "vehicle_inventory.py").read_text(encoding="utf-8") == (
        "CAPABILITY_ID = 'vehicle_inventory'\n"
    )
    # Idempotent.
    assert prepare_pilot_workspace(tmp_path) == []


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
