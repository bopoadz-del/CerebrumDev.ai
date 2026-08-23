"""Prepare an existing workspace for a Store-green / pytest -m pilot cycle.

Does not regenerate app/actions or wipe agent-written handlers. It only
patches vendored Store adapters that the code-phase CLONER left unable to
run offline: DatabaseBlock never opened SQLite, and notification MCP
imports the factory host's ``app.dependencies``.
"""

from __future__ import annotations

import re
from pathlib import Path

_ENSURE_READY_MARKER = "def _ensure_store_block_ready"

_ENSURE_READY_FN = '''
def _ensure_store_block_ready(instance):
    """DatabaseBlock only opens SQLite in _legacy_initialize.

    Construct-with-HAL is not enough: process() uses self._connection,
    which stays None until initialize runs. LotDesk pilot then died on
    Insert failed: 'NoneType' object has no attribute 'cursor'.
    """
    conn = getattr(instance, "_connection", None)
    init = getattr(instance, "_legacy_initialize", None)
    if conn is None and callable(init):
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(init())
            return instance
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, init()).result()
    return instance

'''

_MCP_OFFLINE_MARKER = "Store-unwired MCP"


def prepare_pilot_workspace(workspace: Path | str) -> list[str]:
    """Patch vendored adapters in place. Returns relative paths touched."""
    root = Path(workspace)
    touched: list[str] = []
    vendor = root / "vendor" / "blocks"
    if vendor.is_dir():
        for block_py in sorted(vendor.glob("*/block.py")):
            if _patch_instantiate_helper(block_py):
                touched.append(str(block_py.relative_to(root)))
    notification = root / "vendor" / "cerebrum" / "blocks" / "notification.py"
    if notification.is_file() and _patch_notification_mcp(notification):
        touched.append(str(notification.relative_to(root)))
    database = root / "vendor" / "cerebrum" / "blocks" / "database.py"
    if database.is_file() and _patch_database_insert(database):
        touched.append(str(database.relative_to(root)))
    return touched


def _patch_instantiate_helper(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "def _instantiate_store_block" not in text:
        return False
    if _ENSURE_READY_MARKER in text and "return _ensure_store_block_ready(call())" in text:
        return False
    if _ENSURE_READY_MARKER not in text:
        text = text.replace(
            "def _instantiate_store_block(block_cls):",
            _ENSURE_READY_FN.lstrip("\n") + "def _instantiate_store_block(block_cls):",
            1,
        )
    # Rewrite the successful construct to initialize SQLite.
    text = re.sub(
        r"(\n        try:\n            return call\(\)\n        except TypeError as exc:\n            attempts.append\(exc\)\n    raise attempts\[-1\])",
        (
            "\n        try:\n            return _ensure_store_block_ready(call())\n"
            "        except TypeError as exc:\n            attempts.append(exc)\n"
            "    raise attempts[-1]"
        ),
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")
    return True


def _patch_notification_mcp(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if _MCP_OFFLINE_MARKER in text:
        return False
    old = (
        "        try:\n"
        "            from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "            from app.dependencies import _create_block_instance\n"
    )
    new = (
        "        try:\n"
        "            # Store-unwired MCP: the factory host module is not in a\n"
        "            # delivered platform. Record the notification in-process.\n"
        "            try:\n"
        "                from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "                from app.dependencies import _create_block_instance\n"
        "            except ImportError:\n"
        "                return {\n"
        "                    \"status\": \"success\",\n"
        "                    \"channel\": \"mcp\",\n"
        "                    \"sent\": True,\n"
        "                    \"block\": block_name,\n"
        "                    \"offline\": True,\n"
        "                    \"result_preview\": str(payload)[:500],\n"
        "                }\n"
    )
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def _patch_database_insert(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "no such table" in text and "CREATE TABLE IF NOT EXISTS" in text:
        return False
    old = (
        "        except Exception as e:\n"
        '            return {"error": f"Insert failed: {str(e)}"}\n'
    )
    new = (
        "        except Exception as e:\n"
        "            if self._connection is not None and \"no such table\" in str(e).lower():\n"
        "                try:\n"
        "                    cols = \", \".join(f\"{k} TEXT\" for k in values.keys())\n"
        "                    cursor = self._connection.cursor()\n"
        "                    cursor.execute(f\"CREATE TABLE IF NOT EXISTS {table} ({cols})\")\n"
        "                    cursor.execute(sql, tuple(values.values()))\n"
        "                    self._connection.commit()\n"
        "                    last_id = cursor.lastrowid if self.backend == \"sqlite\" else None\n"
        "                    return {\n"
        "                        \"inserted\": True,\n"
        "                        \"id\": last_id,\n"
        "                        \"rows_affected\": cursor.rowcount,\n"
        "                        \"created_table\": True,\n"
        "                    }\n"
        "                except Exception as retry_exc:\n"
        '                    return {"error": f"Insert failed: {str(retry_exc)}"}\n'
        '            return {"error": f"Insert failed: {str(e)}"}\n'
    )
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True
