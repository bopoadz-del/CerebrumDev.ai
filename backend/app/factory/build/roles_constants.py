"""Literal tables, regexes, and generated-file templates for the build roles.

Extracted from ``roles.py``. String bodies are copied verbatim so emitted
handlers, models, and tester bootstraps do not change.
"""

from __future__ import annotations

import re


#: Any reference to the Store's runtime packages, in code or import strings.
_STORE_RUNTIME_RE = re.compile(r"\bapp\.(blocks|core)\b")
#: A module-level reference to the Store's ``app`` package that is NOT
#: blocks/core -- it executes at import time, so the block cannot load
#: offline at all. Column 0 on purpose: an indented (function-local) import
#: only breaks the one feature that runs it, and is recorded instead.
_STORE_FOREIGN_TOP_RE = re.compile(
    r"^(?:from\s+app(?:\.(?!blocks\b|core\b)[\w.]+)?\s+import|import\s+app\b)",
    re.MULTILINE,
)
#: The same reference inside a function body (indented).
_STORE_FOREIGN_LAZY_RE = re.compile(
    r"^\s+from\s+(app(?:\.(?!blocks\b|core\b)[\w.]+)?)\s+import",
    re.MULTILINE,
)
#: Names importable from ``app.blocks`` that are registry API, not block
#: classes. The generated registry provides them (or core does).
_REGISTRY_API_NAMES = frozenset(
    {
        "get_block",
        "get_all_blocks",
        "get_block_capabilities",
        "BLOCK_REGISTRY",
        "BlockCapabilities",
        "UniversalBlock",
        "UniversalContainer",
        "TypedBlock",
    }
)
#: One entry of the Store registry's literal defs:
#: ``"name": ("app.blocks.module", "ClassName")``. Single or double quotes.
_BLOCK_DEF_RE = re.compile(
    r'''["'](?P<name>[\w-]+)["']\s*:\s*\(\s*["']app\.blocks\.(?P<mod>[\w.]+)["']\s*,\s*["'](?P<cls>\w+)["']\s*\)'''
)
#: ``get_block("formula_executor")`` inside a kit-shelf shim.
_GET_BLOCK_RE = re.compile(r"""get_block\(\s*['"]([\w-]+)['"]""")
#: ``class FormulaExecutorV2(`` in a Store block module.
_BLOCK_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(", re.MULTILINE)

_INSTANTIATE_HELPER = '''
import os as _os
import sqlite3 as _sqlite3
from pathlib import Path as _HalPath


class _OfflineHal:
    """In-process HAL so Store DatabaseBlock can cursor() without a live store.

    Passing None made the tasting-room suite fail with
    ``'NoneType' object has no attribute 'cursor'`` after construct succeeded.
    """

    def __init__(self, db_path):
        self._db_path = db_path
        self._conn = None
        self.config = {}

    def _connect(self):
        if self._conn is None:
            parent = _HalPath(self._db_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            self._conn = _sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = _sqlite3.Row
        return self._conn

    @property
    def connection(self):
        return self._connect()

    def get_connection(self):
        return self._connect()

    def cursor(self):
        return self._connect().cursor()

    def execute(self, *args, **kwargs):
        return self._connect().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._connect().executemany(*args, **kwargs)

    def commit(self):
        self._connect().commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _offline_hal():
    root = _HalPath(_os.environ.get("STORAGE_PATH") or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return _OfflineHal(str(root / "store_blocks.sqlite"))


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


def _instantiate_store_block(block_cls):
    """Store classes take (hal_block, config); kit shims called block_cls()."""
    hal = _offline_hal()
    attempts = []
    for call in (
        lambda: block_cls(hal, {}),
        lambda: block_cls(hal_block=hal, config={}),
        lambda: block_cls(hal=hal, config={}),
        lambda: block_cls(),
    ):
        try:
            return _ensure_store_block_ready(call())
        except TypeError as exc:
            attempts.append(exc)
    raise attempts[-1]
'''

_DISPATCH_RUNTIME = '''"""Local block dispatch. No network, no store, no callback.

The old generated platforms answered a capability by POSTing to the operator's
block store. This imports the block that was vendored into this repository at
build time and calls it in-process, which is what makes the platform runnable
offline and independent of the factory's uptime.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "blocks"
_CACHE: Dict[str, Any] = {}


class BlockNotVendored(RuntimeError):
    """Asked for a block this platform does not carry."""


def load_block(block_id: str):
    if block_id in _CACHE:
        return _CACHE[block_id]
    path = _VENDOR / block_id / "block.py"
    if not path.is_file():
        raise BlockNotVendored(
            f"{block_id} is not vendored in this platform (looked in {path})"
        )
    spec = importlib.util.spec_from_file_location(f"vendored_{block_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _CACHE[block_id] = module
    return module


BLOCK_CONTRACTS: Dict[str, Any] = {}


class DispatchContractError(ValueError):
    """Caller payload failed the harvested contract. Do not invent fields."""


def _required_fields(block_id: str) -> list:
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    required = list(contract.get("input_required_fields") or [])
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and item.get("required") and name not in required:
            required.append(name)
    return [field for field in required if field != "action"]


def _known_fields(block_id: str) -> set:
    """Closed field set from the harvested contract. Empty = no allow-list."""
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    names: set = set()
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and name != "action":
            names.add(name)
    for field in contract.get("input_required_fields") or []:
        if field != "action":
            names.add(field)
    return names


def _adapt_input(block_id: str, payload: Any, action: str | None) -> Dict[str, Any]:
    """Pass the caller payload through. Never invent Store fields (F18)."""
    data = dict(payload) if isinstance(payload, dict) else {"value": payload}
    known = _known_fields(block_id)
    # Store blocks are action-dispatched: the domain record travels in the
    # block's declared ``input`` slot, with block params beside it. A handler
    # that passes the record flat gets every domain key refused as unknown --
    # measured on the warehouse `audit` capability, which declares
    # reference/status/quantity in its own model and had all three rejected.
    #
    # This is adaptation, not F18 fabrication. No value is invented and no
    # field is conjured to satisfy a validator: the caller's own record is
    # placed in the field the block declares for exactly it. If the caller
    # already supplied ``input``, nothing is moved.
    if known and "input" in known and "input" not in data:
        stray = {k: v for k, v in data.items() if k not in known}
        if stray:
            data = {k: v for k, v in data.items() if k in known}
            data["input"] = stray
    missing = [
        field for field in _required_fields(block_id)
        if field not in data or data[field] in (None, "")
    ]
    if missing:
        raise DispatchContractError(
            f"{block_id} missing required field(s): {', '.join(missing)}"
        )
    if known:
        unknown = sorted(str(key) for key in data if key not in known)
        if unknown:
            raise DispatchContractError(
                f"{block_id} unknown field(s): {', '.join(unknown)}"
            )
    return data


def _error_envelope(block_id: str, action: str | None, error: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "block": block_id,
        "action": action,
        "error": error,
        "ok": False,
    }


def _force_utf8_stdio() -> None:
    """F13: a checkmark/emoji print must not become a charmap crash.

    Windows cp1252 consoles encode print() with the console codepage. A
    vendored block that prints one checkmark used to raise UnicodeEncodeError;
    execute() then swallowed that into a generic error envelope that looked
    like a domain refusal. Force UTF-8 on this process before the block runs.
    """
    import os
    import sys

    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            continue


_force_utf8_stdio()


def execute(
    block_id: str,
    payload: Dict[str, Any],
    action: str | None = None,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run a vendored block locally and return its result envelope.

    Real Store blocks are action-dispatched: the domain data travels as
    ``input`` and the operation name as ``action`` (each block declares a
    default in its block.json). A call with no action reaches blocks that
    answer "Unknown action" -- pass the one the capability needs.

    Missing required fields and unknown fields/params become an error
    envelope. A block refusal (status=error) is returned as-is — never
    rewritten to ok.
    """
    module = load_block(block_id)
    run = getattr(module, "run", None)
    if run is None:
        raise BlockNotVendored(f"{block_id} exposes no run() entry point")
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    declared = set()
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            declared.add(name)
    if (
        params
        and (contract.get("declared_inputs") or contract.get("input_required_fields"))
    ):
        extra = sorted(
            str(key) for key in params if key not in declared and key != "action"
        )
        if extra:
            return _error_envelope(
                block_id, action, f"unknown param(s): {', '.join(extra)}"
            )
    kwargs = dict(params or {})
    if action is not None:
        kwargs["action"] = action
    try:
        adapted = _adapt_input(block_id, payload, action)
    except DispatchContractError as exc:
        return _error_envelope(block_id, action, str(exc))
    # A block-level failure comes back as data, not as an exception. The
    # Store's shim raises RuntimeError on an error envelope, which destroys
    # the diagnosis: a handler (and a failing test) sees "Input validation
    # failed" with no block name and no field list. Structural failures --
    # a block that is not vendored -- still raise above.
    _force_utf8_stdio()
    try:
        result = run(input=adapted, **kwargs)
    except BlockNotVendored:
        raise
    except UnicodeEncodeError as exc:
        return _error_envelope(
            block_id,
            action,
            f"UnicodeEncodeError on block stdout (encoding, not domain): {exc}",
        )
    except Exception as exc:
        return _error_envelope(
            block_id, action, f"{type(exc).__name__}: {exc}"
        )
    if isinstance(result, dict) and (
        result.get("status") == "error" or result.get("ok") is False
    ):
        refused = dict(result)
        refused.setdefault("status", "error")
        refused.setdefault("block", block_id)
        refused["ok"] = False
        return refused
    return result
'''

_PY_DEFAULTS = {"str": '""', "int": "0", "float": "0.0", "bool": "False"}

_SAMPLE_VALUES = {"str": "sample", "int": 1, "float": 1.5, "bool": True}

_CONFTEST = '''"""Test bootstrap for the generated platform.

Puts the platform root on sys.path and points persistence at a scratch
directory, so running the suite never touches a real data file.

STORAGE_PATH is FORCED, not defaulted. The build environment legitimately
carries its own STORAGE_PATH (the factory backend sets one), and a
``setdefault`` here made every tester round share one database file: a
table created by round N rejected round N+1's columns, and the rework loop
burned its budget chasing schema errors no round had actually caused.

Outbound network is BLOCKED, not merely unconfigured. Stripping the store
env only proves the platform does not call the store; a handler that posts
to an arbitrary public URL still passed, and one did -- "sent" a webhook to
the open internet from a platform whose whole claim is running offline.
Loopback stays open so TestClient-style local servers keep working.
P1: this blocker is unchanged. Do not add local-inference or cloud hosts.
"""

import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="platform-test-")

# Schema is versioned. connect() does not CREATE TABLE. Apply head so
# model/route tests have tables; a missing revision fails the suite.
# ImportError is only for isolation probes that exec this file without app/.
try:
    from app.migrations import upgrade_head  # noqa: E402

    upgrade_head()
except ImportError:
    pass

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_real_connect = socket.socket.connect


def _offline_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("utf-8", "replace")
    if str(host) not in _LOCAL_HOSTS:
        raise OSError(
            f"offline suite: outbound connection to {host!r} refused -- this "
            "platform must run with no network"
        )
    return _real_connect(self, address)


socket.socket.connect = _offline_connect


def pytest_configure(config):
    """Register the factory vs pilot split. TESTER's lane is tests/** so
    this cannot live in a repo-root pytest.ini."""
    config.addinivalue_line(
        "markers",
        "pilot: Store-backed execute-all; excluded from the factory code-phase gate",
    )
'''
