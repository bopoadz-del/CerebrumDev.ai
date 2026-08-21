"""A clone of a real Store block must carry the runtime it stands on.

New-shape tests for defect 1g. Real Store blocks are shims: block.py does
``from app.blocks import get_block`` and the logic lives in the Store's
``app/blocks/<name>.py``, resting on ``app/core``. The CLONER used to vendor
only the shim, so the first build against the real Store failed its own gate
with ``ModuleNotFoundError: No module named 'app'`` -- six blocks out of six.

The name ``app`` cannot be vendored as-is because the delivered platform's own
package is called ``app``. So the CLONER must vendor the slice under
``vendor/cerebrum/`` and mechanically rewrite ``app.blocks``/``app.core``
imports to the vendored names. These tests drive that behaviour against a faux
Store shaped exactly like the real one.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext, gate_blocks_import_offline
from app.factory.build.roles import RoleContext, RoleError, run_cloner
from app.factory.build.workspace import RoleWorkspace

pytestmark = pytest.mark.usefixtures("_no_paid_calls")


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


_SHIM = '''\
"""Auto-generated adapter for Cerebrum block: greeting."""

import asyncio
from app.blocks import get_block


def run(**kwargs):
    block_cls = get_block("greeting")
    instance = block_cls()
    input_data = kwargs.get("input", kwargs)
    envelope = asyncio.run(instance.execute(input_data, {}))
    return envelope.get("result", envelope)
'''

_BLOCKS_INIT = '''\
import importlib

_EXTENDED_BLOCK_DEFS = {
    "greeting": ("app.blocks.greeting", "GreetingBlock"),
    "farewell": ("app.blocks.farewell", "FarewellBlock"),
}


def get_block(name):
    module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
    return getattr(importlib.import_module(module_path), class_name)
'''

_GREETING = '''\
from app.core.universal_base import UniversalBlock


class GreetingBlock(UniversalBlock):
    async def execute(self, input_data, params):
        name = (input_data or {}).get("name", "world")
        return {"status": "ok", "result": {"greeting": f"hello {name}"}}
'''

_UNIVERSAL_BASE = '''\
class UniversalBlock:
    def __init__(self, hal_block=None, config=None):
        self.hal_block = hal_block
        self.config = config or {}
'''


def _faux_store(root: Path) -> Path:
    """A Store checkout shaped like the real one: shim + app.blocks + app.core."""
    store = root / "store"
    reg = store / "block_registry" / "greeting"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "greeting", "name": "Greeting"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_SHIM, encoding="utf-8")

    blocks = store / "app" / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "__init__.py").write_text(_BLOCKS_INIT, encoding="utf-8")
    (blocks / "greeting.py").write_text(_GREETING, encoding="utf-8")

    core = store / "app" / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "universal_base.py").write_text(_UNIVERSAL_BASE, encoding="utf-8")
    return store


def _clone(tmp_path: Path, store: Path, block_ids=("greeting",)):
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store,
        state={"resolved_blocks": tuple(block_ids)},
    )
    return ws, run_cloner(ctx)


def test_a_real_store_shim_imports_offline_after_cloning(tmp_path):
    """The exact failure of the first real build: the shim's ``from app.blocks``
    must resolve inside the delivered workspace, with no Store checkout and no
    store env configured."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    gate = gate_blocks_import_offline(
        GateContext(
            workspace=ws.destination,
            role=BuildRole.CLONER,
            vendored_blocks=("greeting",),
        )
    )
    assert gate.ok, f"{gate.detail}: {gate.findings}"


def test_the_vendored_shim_executes_offline(tmp_path):
    """Importing is not the bar -- get_block must resolve through the vendored
    registry and the block must run, in a subprocess whose only world is the
    workspace."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib, sys
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/greeting/block.py")
        spec = importlib.util.spec_from_file_location("vendored_greeting", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.run(input={"name": "site"})
        print(json.dumps(out))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"greeting": "hello site"}


def test_the_lockfile_records_the_runtime_slice(tmp_path):
    """The registrar cannot answer staleness for files it does not know were
    cloned. The slice is cloned material and must be in the lockfile."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    lock = json.loads((ws.destination / "blocks.lock.json").read_text(encoding="utf-8"))
    runtime = lock.get("runtime")
    assert runtime, "lockfile has no runtime-slice record"
    assert runtime["source"] == "cerebrum-blocks"
    assert runtime.get("commit"), "runtime slice is unpinned"
    files = runtime.get("files", [])
    assert "vendor/cerebrum/blocks/greeting.py" in files
    assert "vendor/cerebrum/core/universal_base.py" in files


def test_the_vendored_registry_lists_only_what_was_cloned(tmp_path):
    """The Store's registry names ~120 modules; the platform carries the ones
    it vendored. A registry entry pointing at a module that is not on disk is
    a latent ModuleNotFoundError in the customer's environment."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    registry = (ws.destination / "vendor" / "cerebrum" / "blocks" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "greeting" in registry
    assert "farewell" not in registry, "registry names a block that was never vendored"



def test_a_standalone_block_vendors_no_runtime_slice(tmp_path):
    """Mirror-style blocks import nothing from the Store runtime; vendoring a
    slice they do not use would ship dead code into every platform."""
    store = _faux_store(tmp_path)
    standalone = store / "block_registry" / "solo"
    standalone.mkdir(parents=True)
    (standalone / "block.json").write_text(json.dumps({"id": "solo"}), encoding="utf-8")
    (standalone / "block.py").write_text(
        "def run(**kwargs):\n    return {'ok': True}\n", encoding="utf-8"
    )

    ws, result = _clone(tmp_path, store, block_ids=("solo",))
    assert result.ok, result.detail
    assert not (ws.destination / "vendor" / "cerebrum").exists()


def test_block_registry_is_exported_for_cross_block_dispatch(tmp_path):
    """Real blocks do ``from app.blocks import BLOCK_REGISTRY`` inside
    functions (workflow chain validation, notification MCP channel). The
    vendored registry must export a lazy BLOCK_REGISTRY over the vendored
    defs, and the parser must not choke on the import line."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            '''
            def peers():
                from app.blocks import BLOCK_REGISTRY
                return "greeting" in BLOCK_REGISTRY
            '''
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    probe = textwrap.dedent(
        """
        from vendor.cerebrum.blocks import BLOCK_REGISTRY, get_block
        assert "greeting" in BLOCK_REGISTRY
        assert BLOCK_REGISTRY["greeting"] is get_block("greeting")
        assert BLOCK_REGISTRY.get("farewell") is None
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_a_lazy_foreign_import_is_recorded_not_fatal(tmp_path):
    """``from app.dependencies import ...`` inside a function only breaks the
    one feature that runs it. Failing the whole clone for an optional path
    would make every real block unbuildable; hiding it would ship a surprise.
    It is recorded in the lockfile."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            '''
            def mcp_channel():
                from app.dependencies import create_instance
                return create_instance
            '''
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    lock = json.loads((ws.destination / "blocks.lock.json").read_text(encoding="utf-8"))
    recorded = lock["runtime"].get("lazy_foreign_imports", [])
    assert any("app.dependencies" in entry for entry in recorded), recorded


def test_a_module_level_foreign_import_fails_the_clone(tmp_path):
    """A top-level import of an unvendorable Store package executes at import
    time -- the block cannot load offline at all, so the clone must fail with
    the module named."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        "from app.dependencies import create_instance\n" + _GREETING,
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match="app.dependencies"):
        _clone(tmp_path, store)


def test_an_unresolvable_runtime_import_fails_the_clone_loudly(tmp_path):
    """A block whose runtime module cannot be found must fail the CLONE with
    the module named -- not pass the clone and fail as a ModuleNotFoundError
    on the customer's machine."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        "from app.core.does_not_exist import Missing\n"
        "from app.core.universal_base import UniversalBlock\n"
        "class GreetingBlock(UniversalBlock):\n"
        "    pass\n",
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match="does_not_exist"):
        _clone(tmp_path, store)

def test_the_slice_follows_what_the_block_needs_not_where_it_came_from(tmp_path):
    """The runtime-slice decision was gated on blocks_root being set, and the
    factory's OWN vendor mirror contains real Store shims (audit/, capture/).
    So a build with no Store checkout vendored a shim importing app.blocks,
    shipped no runtime for it, and failed the CLONER gate with "No module
    named 'app'" -- on the production default path.

    With no Store checkout the clone must now REFUSE and name the fix, rather
    than produce an artifact that cannot import.
    """
    store = _faux_store(tmp_path)
    mirror_style = store / "block_registry" / "needs_runtime"
    mirror_style.mkdir(parents=True)
    (mirror_style / "block.json").write_text(
        json.dumps({"id": "needs_runtime"}), encoding="utf-8"
    )
    (mirror_style / "block.py").write_text(_SHIM, encoding="utf-8")

    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "no-store-build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=None,  # no Store checkout available
        state={"resolved_blocks": ("needs_runtime",)},
    )

    # Point the mirror lookup at our shim so the source resolves without a
    # blocks_root, exactly as the real vendor mirror does.
    import app.factory.build.roles as roles_mod

    original = roles_mod._block_source_dir
    roles_mod._block_source_dir = lambda bid, root: mirror_style
    try:
        with pytest.raises(RoleError, match="CEREBRUM_BLOCKS_ROOT"):
            run_cloner(ctx)
    finally:
        roles_mod._block_source_dir = original


_FORMULA_SHIM = '''\
"""Auto-generated adapter for Cerebrum block: formula_executor."""

import asyncio
from app.blocks import get_block


def run(**kwargs):
    block_cls = get_block("formula_executor")
    instance = block_cls()
    input_data = kwargs.get("input", kwargs)
    envelope = asyncio.run(instance.execute(input_data, {}))
    return envelope.get("result", envelope)
'''

_FORMULA_V2 = '''\
from app.core.universal_base import UniversalBlock


class FormulaExecutorV2(UniversalBlock):
    async def execute(self, input_data, params):
        expr = (input_data or {}).get("expr", "ok")
        return {"status": "ok", "result": {"value": expr}}
'''


def test_cloner_aliases_kit_id_to_store_v2_registry(tmp_path):
    """Live tasting-room Approve: kit id ``formula_executor``, Store registry
    only lists ``formula_executor_v2``. CLONER used to fail with "has no
    entry for it" instead of vendoring the v2 module under the kit name."""
    store = _faux_store(tmp_path)
    reg = store / "block_registry" / "formula_executor"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "formula_executor"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_FORMULA_SHIM, encoding="utf-8")
    (store / "app" / "blocks" / "__init__.py").write_text(
        textwrap.dedent(
            '''
            import importlib

            _EXTENDED_BLOCK_DEFS = {
                "formula_executor_v2": ("app.blocks.formula_executor_v2", "FormulaExecutorV2"),
            }

            def get_block(name):
                module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
                return getattr(importlib.import_module(module_path), class_name)
            '''
        ),
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "formula_executor_v2.py").write_text(
        _FORMULA_V2, encoding="utf-8"
    )

    ws, result = _clone(tmp_path, store, block_ids=("formula_executor",))
    assert result.ok, result.detail

    registry = (ws.destination / "vendor" / "cerebrum" / "blocks" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "formula_executor" in registry
    assert "formula_executor_v2" in registry

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib, sys
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/formula_executor/block.py")
        spec = importlib.util.spec_from_file_location("vendored_formula", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.run(input={"expr": 42})
        print(json.dumps(out))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"value": 42}


def test_unregistered_store_shim_falls_back_to_factory_mirror(tmp_path):
    """If the Store has a kit-shelf shim, no registry entry, and no ``_v2``
    module, CLONER must vendor the factory stub instead of dying."""
    store = _faux_store(tmp_path)
    reg = store / "block_registry" / "formula_executor"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "formula_executor"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_FORMULA_SHIM, encoding="utf-8")

    ws, result = _clone(tmp_path, store, block_ids=("formula_executor",))
    assert result.ok, result.detail
    vendored = (ws.destination / "vendor" / "blocks" / "formula_executor" / "block.py").read_text(
        encoding="utf-8"
    )
    assert "factory-vendor-mirror stub" in vendored
    assert "get_block" not in vendored
    lock = json.loads((ws.destination / "blocks.lock.json").read_text(encoding="utf-8"))
    assert lock["blocks"]["formula_executor"]["source"] == "factory-vendor-mirror"
    assert "runtime" not in lock


def test_store_registry_single_quotes_still_parse(tmp_path):
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "__init__.py").write_text(
        textwrap.dedent(
            """
            import importlib

            _EXTENDED_BLOCK_DEFS = {
                'greeting': ('app.blocks.greeting', 'GreetingBlock'),
            }

            def get_block(name):
                module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
                return getattr(importlib.import_module(module_path), class_name)
            """
        ),
        encoding="utf-8",
    )
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail
    registry = (ws.destination / "vendor" / "cerebrum" / "blocks" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "greeting" in registry


def test_cloner_rewrites_zero_arg_store_constructors(tmp_path):
    """Live TESTER: DatabaseBlock.__init__ required hal_block and config,
    kit shims called block_cls(), and every capability died on construct."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        textwrap.dedent(
            """
            class GreetingBlock:
                def __init__(self, hal_block, config):
                    self.hal_block = hal_block
                    self.config = config

                async def execute(self, input_data, params):
                    return {"status": "ok", "result": {"greeting": "hello"}}
            """
        ),
        encoding="utf-8",
    )
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail
    shim = (ws.destination / "vendor" / "blocks" / "greeting" / "block.py").read_text(
        encoding="utf-8"
    )
    assert "_instantiate_store_block" in shim
    assert "instance = block_cls()" not in shim

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/greeting/block.py")
        spec = importlib.util.spec_from_file_location("vendored_greeting", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(json.dumps(module.run(input={"name": "site"})))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"greeting": "hello"}

