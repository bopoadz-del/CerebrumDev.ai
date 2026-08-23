"""Resume-to-pilot must patch Store-unwired adapters without wiping WRITER files."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.pilot import prepare_pilot_workspace


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
        '            return {"error": f"Insert failed: {str(e)}"}\n',
        encoding="utf-8",
    )

    touched = prepare_pilot_workspace(tmp_path)
    assert "vendor/blocks/database/block.py" in touched
    assert "vendor/cerebrum/blocks/notification.py" in touched
    assert "vendor/cerebrum/blocks/database.py" in touched
    assert not any(p.startswith("app/") for p in touched)

    helper = (block / "block.py").read_text(encoding="utf-8")
    assert "_ensure_store_block_ready" in helper
    assert "return _ensure_store_block_ready(call())" in helper
    mcp = (notify / "notification.py").read_text(encoding="utf-8")
    assert "Store-unwired MCP" in mcp
    assert "offline" in mcp
    db = (notify / "database.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" in db
    assert (actions / "vehicle_inventory.py").read_text(encoding="utf-8") == (
        "CAPABILITY_ID = 'vehicle_inventory'\n"
    )
    # Idempotent.
    assert prepare_pilot_workspace(tmp_path) == []
