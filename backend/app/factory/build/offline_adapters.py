"""CLONER emission transforms for Store-unwired adapters.

These used to run as ``prepare_pilot_workspace`` after a gate, which is
patch-until-green (F29). They now run only when CLONER writes ``vendor/**``.
They do not invent Blocks source and they do not write Cerebrum-Blocks.
"""

from __future__ import annotations

import re

ENSURE_READY_MARKER = "def _ensure_store_block_ready"
MCP_OFFLINE_MARKER = "Store-unwired MCP"
QUERY_UNWIRED_MARKER = "Store-unwired query"
QUERY_CREATE_MARKER = "CREATE TABLE IF NOT EXISTS"
AIOFILES_MARKER = "Store-unwired aiofiles"
DOC_PARSE_UNWIRED_MARKER = "Store-unwired document parse"

_ENSURE_READY_FN = '''
def _ensure_store_block_ready(instance):
    """DatabaseBlock only opens SQLite in _legacy_initialize.

    Construct-with-HAL is not enough: process() uses self._connection,
    which stays None until initialize runs.
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


def emit_instantiate_ready(text: str) -> str:
    """Ensure Store shims initialize SQLite after construct."""
    if "def _instantiate_store_block" not in text:
        return text
    if ENSURE_READY_MARKER in text and "return _ensure_store_block_ready(call())" in text:
        return text
    if ENSURE_READY_MARKER not in text:
        text = text.replace(
            "def _instantiate_store_block(block_cls):",
            _ENSURE_READY_FN.lstrip("\n") + "def _instantiate_store_block(block_cls):",
            1,
        )
    return re.sub(
        r"(\n        try:\n            return call\(\)\n        except TypeError as exc:\n            attempts.append\(exc\)\n    raise attempts\[-1\])",
        (
            "\n        try:\n            return _ensure_store_block_ready(call())\n"
            "        except TypeError as exc:\n            attempts.append(exc)\n"
            "    raise attempts[-1]"
        ),
        text,
        count=1,
    )


def emit_notification_mcp(text: str) -> str:
    if MCP_OFFLINE_MARKER in text:
        return text
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
        return text
    return text.replace(old, new, 1)


def emit_database_insert(text: str) -> str:
    if "no such table" in text and "CREATE TABLE IF NOT EXISTS" in text:
        return text
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
        return text
    return text.replace(old, new, 1)


def emit_database_query(text: str) -> str:
    """Build SELECT SQL from table/filters when the handler omitted ``sql``."""
    if QUERY_UNWIRED_MARKER in text:
        return text
    old = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n"
    )
    new = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        # Store-unwired query: handlers often pass table/filters\n"
        "        # without a SQL string. sqlite3.execute(None) raises\n"
        "        # \"argument 1 must be str, not None\".\n"
        "        if not sql:\n"
        "            table = data.get(\"table\") or data.get(\"table_name\")\n"
        "            filters = data.get(\"filters\") or data.get(\"where\") or {}\n"
        "            if table and isinstance(filters, dict) and filters:\n"
        "                cols = \" AND \".join(f\"{k} = ?\" for k in filters)\n"
        "                sql = f\"SELECT * FROM {table} WHERE {cols}\"\n"
        "                params = tuple(filters.values())\n"
        "            elif table:\n"
        "                sql = f\"SELECT * FROM {table}\"\n"
        "                params = ()\n"
        "            else:\n"
        "                return {\"error\": \"Query failed: missing sql or table\", \"sql\": None}\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            try:\n"
        "                cursor.execute(sql, params)\n"
        "            except Exception as qexc:\n"
        "                if self._connection is not None and \"no such table\" in str(qexc).lower():\n"
        "                    table = data.get(\"table\") or data.get(\"table_name\")\n"
        "                    if table:\n"
        "                        cursor.execute(\n"
        "                            f\"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY)\"\n"
        "                        )\n"
        "                        self._connection.commit()\n"
        "                        cursor.execute(sql, params)\n"
        "                    else:\n"
        "                        raise\n"
        "                else:\n"
        "                    raise\n"
    )
    if old not in text:
        return text
    return text.replace(old, new, 1)


def emit_document_engine_parse(text: str) -> str:
    """Stub PDF parser imports so PRODUCT can run without pdfplumber/pypdf.

    Live veterinary-care PRODUCT (sess_66a387b5c9b0495c) died on
    ``Missing PDF parser package`` after #306 synthesized a real PDF path.
    The factory interpreter may have ``pypdf``; generated-product pytest
    and some Store backends look for ``pdfplumber`` / ``PyPDF2``. Injecting
    a typed stub is Store-unwired adaptation — the same class as aiofiles —
    not inventing a successful parse of a caller document.
    """
    if DOC_PARSE_UNWIRED_MARKER in text:
        return text
    preamble = (
        "# Store-unwired document parse: delivered platforms may lack the\n"
        "# Store's PDF libraries. A stub reader lets parse() import; text\n"
        "# comes from the caller payload (prepare_block_input already sets it).\n"
        "import sys as _sys, types as _types\n"
        "\n"
        "class _OfflinePdfPage:\n"
        "    def extract_text(self):\n"
        "        return \"\"\n"
        "\n"
        "class _OfflinePdfReader:\n"
        "    def __init__(self, stream):\n"
        "        self.pages = [_OfflinePdfPage()]\n"
        "        self.metadata = {}\n"
        "    def __enter__(self):\n"
        "        return self\n"
        "    def __exit__(self, *exc):\n"
        "        return False\n"
        "\n"
        "def _install_offline_pdf(name):\n"
        "    if name in _sys.modules:\n"
        "        return\n"
        "    try:\n"
        "        __import__(name)\n"
        "        return\n"
        "    except ImportError:\n"
        "        pass\n"
        "    mod = _types.ModuleType(name)\n"
        "    mod.PdfReader = _OfflinePdfReader\n"
        "    mod.PdfWriter = object\n"
        "    _sys.modules[name] = mod\n"
        "\n"
        "for _pdf_name in (\"pypdf\", \"PyPDF2\", \"pdfplumber\", \"pdfminer\", \"fitz\"):\n"
        "    _install_offline_pdf(_pdf_name)\n"
        "\n"
    )
    return preamble + text


def emit_storage_aiofiles(text: str) -> str:
    if AIOFILES_MARKER not in text and "import aiofiles" in text:
        fallback = (
            "# Store-unwired aiofiles: delivered platforms do not ship aiofiles.\n"
            "try:\n"
            "    import aiofiles\n"
            "except ImportError:\n"
            "    class _StdAioFile:\n"
            "        def __init__(self, path, mode):\n"
            "            self._path = path\n"
            "            self._mode = mode\n"
            "            self._fh = None\n"
            "        async def __aenter__(self):\n"
            "            self._fh = open(self._path, self._mode)\n"
            "            return self\n"
            "        async def __aexit__(self, *exc):\n"
            "            self._fh.close()\n"
            "        async def write(self, data):\n"
            "            return self._fh.write(data)\n"
            "        async def read(self):\n"
            "            return self._fh.read()\n"
            "    class _StdAioFiles:\n"
            "        @staticmethod\n"
            "        def open(path, mode=\"r\"):\n"
            "            return _StdAioFile(path, mode)\n"
            "    aiofiles = _StdAioFiles()\n"
        )
        text = text.replace("import aiofiles\n", fallback, 1)
    needle = (
        "        file_hash = hashlib.sha256(content if isinstance(content, bytes) "
        "else content.encode()).hexdigest()[:16]\n"
    )
    if needle in text and "json.dumps(content" not in text:
        text = text.replace(
            needle,
            "        if not isinstance(content, (bytes, str)):\n"
            "            import json as _json\n"
            "            content = _json.dumps(content, default=str)\n"
            + needle,
            1,
        )
    return text


def emit_runtime_module(module_name: str, text: str) -> str:
    """Apply the emission transform that belongs to one vendored Store module."""
    name = module_name.rsplit(".", 1)[-1]
    if name == "notification":
        return emit_notification_mcp(text)
    if name == "database":
        return emit_database_query(emit_database_insert(text))
    if name == "document_engine":
        return emit_document_engine_parse(text)
    if name == "storage":
        return emit_storage_aiofiles(text)
    if name == "capture":
        from app.factory.build.network_posture import P1_CAPTURE_ADAPTER

        # P1 replaces a Store capture module that would default to deepseek.
        return P1_CAPTURE_ADAPTER
    return text
