"""The build roles. Each does one job, inside one lane, judged by one gate.

These are the minimum set needed to drive a real end-to-end build. They are
deliberately *not* thin wrappers around the template generator: the CLONER
vendors real block source and the WRITER writes handlers that **import that
source locally**, rather than emitting the ``httpx.post(store_url +
"/v1/execute")`` callback the old ProductGenerator path produces. That
difference is the point of the rebuild -- a delivered platform that runs
without the operator's store being up.

LLM use is optional by design. When a coder key is configured the WRITER asks
it for the handler body; when it is not, the WRITER composes the body from the
block's declared contract. Both paths write the same *shape*, so CI exercises
the real manufacturing route with no API key and no non-deterministic output.
Which path ran is recorded, never implied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.factory.build.authority import BuildRole
from app.factory.build.workspace import RoleWorkspace


@dataclass
class RoleContext:
    """Everything a role is given. Nothing is reachable except through this."""

    role: BuildRole
    workspace: RoleWorkspace
    blueprint: Any
    plan: Any
    blocks_root: Optional[Path] = None
    #: Findings from the gate that sent this role back round. The WRITER's
    #: work list on a rework pass; empty on a first pass.
    work_list: Sequence[str] = ()
    #: Carried forward between phases (gaps from COLLECTOR, blocks from CLONER).
    state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoleResult:
    ok: bool
    detail: str = ""
    #: Merged into the shared state and into the next GateContext.
    gaps: tuple = ()
    vendored_blocks: tuple = ()
    notes: Dict[str, Any] = field(default_factory=dict)


class RoleError(RuntimeError):
    """A role could not do its job. Never swallowed into a partial success."""


# -- COLLECTOR -----------------------------------------------------------


def run_collector(ctx: RoleContext) -> RoleResult:
    """Resolve planned capabilities into concrete parts, and name every gap.

    Read-only by contract: this writes nothing. A capability the plan could
    not back with a block is reported as a gap so the WRITER knows it must
    author that logic -- silently dropping it is the failure mode the
    COLLECTOR gate exists to catch.
    """
    resolved: List[str] = []
    gaps: List[str] = []
    for cap in ctx.plan.capabilities:
        if cap.block_ids:
            resolved.extend(cap.block_ids)
        else:
            gaps.append(cap.capability_id)

    if not resolved and not gaps:
        raise RoleError("plan contains no capabilities — nothing to build")

    # De-duplicate while keeping a stable order, so the manifest and every
    # downstream hash are reproducible.
    seen: Dict[str, None] = {}
    for bid in resolved:
        seen.setdefault(bid, None)

    return RoleResult(
        ok=True,
        detail=f"{len(seen)} block(s) resolved, {len(gaps)} gap(s)",
        gaps=tuple(gaps),
        vendored_blocks=tuple(seen),
        notes={"resolved_blocks": list(seen), "gaps": gaps},
    )


# -- CLONER --------------------------------------------------------------


def _block_source_dir(block_id: str, blocks_root: Optional[Path]) -> Optional[Path]:
    """Real Store checkout first, factory vendor mirror second."""
    if blocks_root:
        candidate = Path(blocks_root) / "block_registry" / block_id
        if (candidate / "block.py").is_file():
            return candidate
    mirror = Path(__file__).resolve().parents[1] / "vendor_blocks_mirror" / block_id
    if (mirror / "block.py").is_file():
        return mirror
    return None


def _content_digest(source: Path) -> str:
    """Stable digest of a block's files, for a source with no commit.

    The vendor mirror is not a git repository, so a mirror-sourced clone has
    no revision to pin. Hashing the content gives the registrar something that
    still changes when the block does, which is what staleness detection
    actually needs.
    """
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(source).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()[:40]


def _pin_source(source: Path, blocks_root: Optional[Path]) -> tuple:
    """(origin, revision) for one vendored block.

    Clones used to be recorded as "unpinned", which left the Store registrar
    unable to answer its own question -- once a platform ships, the commit a
    block came from is unrecoverable, because the vendored files look
    identical whether they are current or eight months behind.
    """
    from app.factory.generator import git_head

    if blocks_root and str(source).startswith(str(Path(blocks_root).resolve())):
        revision = git_head(Path(blocks_root))
        # A checkout that is not a git repo answers "unknown"; fall back to
        # content rather than recording a revision that means nothing.
        if revision and revision != "unknown":
            return "cerebrum-blocks", revision
        return "cerebrum-blocks", _content_digest(source)
    return "factory-vendor-mirror", _content_digest(source)


# -- runtime slice (defect 1g) -------------------------------------------
#
# Real Store blocks are shims: block.py does ``from app.blocks import
# get_block`` and the logic lives in the Store's ``app/blocks/<name>.py``,
# resting on ``app/core``. The first build against the real Store failed its
# own offline gate on every block with ``No module named 'app'`` because only
# the shim was vendored. The name ``app`` cannot be vendored as-is -- the
# delivered platform's own package is ``app`` -- so the slice lives under
# ``vendor/cerebrum/`` and every ``app.blocks``/``app.core`` import in copied
# source is mechanically rewritten to the vendored name.

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
#: ``"name": ("app.blocks.module", "ClassName")``.
_BLOCK_DEF_RE = re.compile(
    r'"(?P<name>[\w-]+)"\s*:\s*\(\s*"app\.blocks\.(?P<mod>[\w.]+)"\s*,\s*"(?P<cls>\w+)"\s*\)'
)


def _rewrite_runtime_imports(text: str) -> str:
    text = re.sub(r"\bapp\.blocks\b", "vendor.cerebrum.blocks", text)
    return re.sub(r"\bapp\.core\b", "vendor.cerebrum.core", text)


def _store_block_defs(blocks_root: Path) -> Dict[str, tuple]:
    """block id -> (module name, class name), parsed from the Store registry's
    literal defs. Parsed, not imported: importing the Store's ``app`` package
    into the factory process would drag in its whole runtime."""
    init = blocks_root / "app" / "blocks" / "__init__.py"
    if not init.is_file():
        return {}
    text = init.read_text(encoding="utf-8")
    return {
        m.group("name"): (m.group("mod"), m.group("cls"))
        for m in _BLOCK_DEF_RE.finditer(text)
    }


def _shim_needs_runtime(source: Path) -> bool:
    return any(
        _STORE_RUNTIME_RE.search(p.read_text(encoding="utf-8", errors="replace"))
        for p in source.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _closure_over_runtime(
    blocks_root: Path, block_ids: Sequence[str], defs: Dict[str, tuple]
) -> tuple:
    """(block modules, core modules) the vendored blocks transitively need.

    Every referenced module must exist; a reference the closure cannot
    resolve fails the clone here, with the module named, rather than passing
    the clone and surfacing as a ModuleNotFoundError on the customer's
    machine.
    """
    blocks_dir = blocks_root / "app" / "blocks"
    core_dir = blocks_root / "app" / "core"
    class_to_name = {cls: name for name, (_, cls) in defs.items()}

    block_mods: Dict[str, None] = {}
    core_mods: Dict[str, None] = {}
    todo: List[str] = []

    for bid in block_ids:
        if bid not in defs:
            raise RoleError(
                f"{bid}: shim imports the Store runtime but the Store registry "
                f"({blocks_dir / '__init__.py'}) has no entry for it"
            )
        todo.append(defs[bid][0])

    while todo:
        mod = todo.pop()
        if mod in block_mods:
            continue
        if "." in mod:
            raise RoleError(
                f"runtime slice cannot vendor app.blocks.{mod}: subpackage "
                "blocks are not supported by the slice vendorer yet"
            )
        path = blocks_dir / f"{mod}.py"
        if not path.is_file():
            raise RoleError(
                f"runtime slice needs app/blocks/{mod}.py which does not "
                "exist in the Store checkout"
            )
        block_mods[mod] = None
        text = path.read_text(encoding="utf-8", errors="replace")
        core_mods.update(dict.fromkeys(re.findall(r"\bapp\.core\.(\w+)\b", text)))
        todo.extend(re.findall(r"\bapp\.blocks\.(\w+)\b", text))
        # Line-bounded on purpose: ``[\w,\s]+`` would swallow the next line.
        for cls in re.findall(r"from\s+app\.blocks\s+import\s+([^\n(#]+)", text):
            for name in (c.strip() for c in cls.split(",")):
                if not name or name in _REGISTRY_API_NAMES:
                    continue
                ref = class_to_name.get(name)
                if ref is None:
                    raise RoleError(
                        f"app/blocks/{mod}.py imports {name} from app.blocks "
                        "but the Store registry maps no block to that class"
                    )
                todo.append(defs[ref][0])

    seen_core: Dict[str, None] = {}
    core_todo = list(core_mods)
    while core_todo:
        name = core_todo.pop()
        if name in seen_core:
            continue
        path = core_dir / f"{name}.py"
        if not path.is_file():
            raise RoleError(
                f"runtime slice needs app/core/{name}.py which does not "
                "exist in the Store checkout"
            )
        seen_core[name] = None
        text = path.read_text(encoding="utf-8", errors="replace")
        core_todo.extend(re.findall(r"\bapp\.core\.(\w+)\b", text))
        core_todo.extend(re.findall(r"^\s*from\s+\.(\w+)\s+import", text, re.MULTILINE))

    return tuple(block_mods), tuple(seen_core)


def _check_foreign_app_imports(rel: str, text: str) -> List[str]:
    """Fail on a module-level foreign ``app`` import; return the lazy ones.

    A top-level import executes when the block loads, so the block cannot
    import offline at all -- the clone must fail with the module named. An
    indented one only breaks the single feature that runs it; it is returned
    so the lockfile records the limitation instead of shipping a surprise.
    """
    leftover = _STORE_FOREIGN_TOP_RE.search(text)
    if leftover:
        raise RoleError(
            f"{rel} imports the Store's app package outside blocks/core at "
            f"module level ({leftover.group(0).strip()!r}); the factory "
            "cannot vendor that"
        )
    return [
        f"{rel}: {module}"
        for module in _STORE_FOREIGN_LAZY_RE.findall(text)
    ]


def _render_vendored_registry(entries: Dict[str, tuple]) -> str:
    defs = "\n".join(
        f'    "{name}": ("vendor.cerebrum.blocks.{mod}", "{cls}"),'
        for name, (mod, cls) in sorted(entries.items())
    )
    return f'''"""Vendored Cerebrum block runtime registry.

Generated by the factory CLONER. Lists ONLY the blocks vendored into this
platform -- an entry pointing at a module that is not on disk would be a
latent ModuleNotFoundError in the customer's environment.
"""

import importlib

_BLOCK_DEFS = {{
{defs}
}}


def get_block(name):
    try:
        module_path, class_name = _BLOCK_DEFS[name]
    except KeyError:
        raise KeyError(f"block {{name!r}} is not vendored in this platform") from None
    return getattr(importlib.import_module(module_path), class_name)


class _LazyRegistry:
    """Mapping-shaped view over the vendored defs. Blocks use it for
    cross-block dispatch (``"x" in BLOCK_REGISTRY``, ``BLOCK_REGISTRY[x]``)."""

    def __contains__(self, name):
        return name in _BLOCK_DEFS

    def __getitem__(self, name):
        return get_block(name)

    def get(self, name, default=None):
        try:
            return get_block(name)
        except KeyError:
            return default

    def keys(self):
        return _BLOCK_DEFS.keys()


BLOCK_REGISTRY = _LazyRegistry()

_CLASS_TO_NAME = {{cls: name for name, (_, cls) in _BLOCK_DEFS.items()}}


def __getattr__(name):
    block = _CLASS_TO_NAME.get(name)
    if block is None:
        raise AttributeError(name)
    return get_block(block)
'''


def _vendor_runtime_slice(
    ctx: RoleContext, runtime_block_ids: Sequence[str]
) -> tuple:
    """Vendor the Store runtime the given blocks stand on.

    Returns ``(files, lazy_foreign_imports)`` -- the workspace-relative paths
    written and any function-local imports of unvendorable Store packages,
    both for the lockfile.
    """
    blocks_root = Path(ctx.blocks_root)
    defs = _store_block_defs(blocks_root)
    block_mods, core_mods = _closure_over_runtime(blocks_root, runtime_block_ids, defs)

    written: List[str] = []
    lazy_foreign: List[str] = []

    def _write(rel: Path, text: str) -> None:
        lazy_foreign.extend(_check_foreign_app_imports(rel.as_posix(), text))
        ctx.workspace.write_text(rel, text)
        written.append(rel.as_posix())

    base = Path("vendor") / "cerebrum"
    _write(
        base / "__init__.py",
        '"""Cerebrum Store runtime, vendored at build time (see blocks.lock.json)."""\n',
    )
    _write(
        base / "core" / "__init__.py",
        '"""Vendored slice of the Store\'s app.core. Deliberately minimal."""\n',
    )
    for name in sorted(core_mods):
        source = (blocks_root / "app" / "core" / f"{name}.py").read_text(
            encoding="utf-8", errors="replace"
        )
        _write(base / "core" / f"{name}.py", _rewrite_runtime_imports(source))

    registry = {
        bid: defs[bid] for bid in runtime_block_ids
    }
    _write(base / "blocks" / "__init__.py", _render_vendored_registry(registry))
    for mod in sorted(block_mods):
        source = (blocks_root / "app" / "blocks" / f"{mod}.py").read_text(
            encoding="utf-8", errors="replace"
        )
        _write(base / "blocks" / f"{mod}.py", _rewrite_runtime_imports(source))

    # The shims themselves still say ``from app.blocks import get_block`` --
    # rewrite them in place to point at the vendored runtime.
    for bid in runtime_block_ids:
        shim_dir = Path("vendor") / "blocks" / bid
        for py in sorted(
            (ctx.workspace.workspace / shim_dir).rglob("*.py")
        ):
            if "__pycache__" in py.parts:
                continue
            rel = shim_dir / py.relative_to(ctx.workspace.workspace / shim_dir)
            text = _rewrite_runtime_imports(py.read_text(encoding="utf-8", errors="replace"))
            lazy_foreign.extend(_check_foreign_app_imports(rel.as_posix(), text))
            ctx.workspace.write_text(rel, text)

    return written, sorted(set(lazy_foreign))


def _runtime_pin(blocks_root: Path) -> str:
    from app.factory.generator import git_head

    revision = git_head(Path(blocks_root))
    if revision and revision != "unknown":
        return revision
    return _content_digest(Path(blocks_root) / "app")


def run_cloner(ctx: RoleContext) -> RoleResult:
    """Vendor each resolved block's real source into the workspace.

    Writes only under ``vendor/`` plus the lockfile -- its whole lane. The
    lockfile records where each block came from so the Store registrar can
    later tell a Store-sourced clone from a mirror stub.
    """
    block_ids = tuple(ctx.state.get("resolved_blocks", ()))
    if not block_ids:
        # A plan of pure-GENERATE capabilities is legitimate; there is simply
        # nothing to vendor. Say so rather than inventing an empty lockfile.
        return RoleResult(ok=True, detail="no blocks to vendor", vendored_blocks=())

    lock: Dict[str, Any] = {"schema": "blocks.lock.v1", "blocks": {}}
    vendored: List[str] = []
    missing: List[str] = []
    runtime_blocks: List[str] = []

    for bid in block_ids:
        source = _block_source_dir(bid, ctx.blocks_root)
        if source is None:
            missing.append(bid)
            continue
        ctx.workspace.copy_tree(source, Path("vendor") / "blocks" / bid)
        origin, revision = _pin_source(source, ctx.blocks_root)
        lock["blocks"][bid] = {
            "source": origin,
            "commit": revision,
            "path": f"vendor/blocks/{bid}",
        }
        vendored.append(bid)
        # A shim that reaches for the Store's runtime cannot import offline
        # on its own -- the slice it stands on must be vendored with it.
        if ctx.blocks_root and _shim_needs_runtime(source):
            runtime_blocks.append(bid)

    if missing:
        raise RoleError(
            "no source found for block(s): " + ", ".join(sorted(missing))
        )

    if runtime_blocks:
        runtime_files, lazy_foreign = _vendor_runtime_slice(ctx, runtime_blocks)
        lock["runtime"] = {
            "source": "cerebrum-blocks",
            "commit": _runtime_pin(Path(ctx.blocks_root)),
            "path": "vendor/cerebrum",
            "for_blocks": sorted(runtime_blocks),
            "files": sorted(runtime_files),
            # Function-local imports of Store packages the factory cannot
            # vendor. Each breaks only the feature that runs it; listed so
            # the limitation ships in the artifact instead of as a surprise.
            "lazy_foreign_imports": lazy_foreign,
        }

    ctx.workspace.write_text(
        "blocks.lock.json", json.dumps(lock, indent=2, sort_keys=True) + "\n"
    )
    ctx.workspace.write_text(
        Path("vendor") / "__init__.py",
        '"""Vendored block source. Imported locally; never fetched at runtime."""\n',
    )
    ctx.workspace.write_text(
        Path("vendor") / "blocks" / "__init__.py",
        '"""Blocks vendored at build time, pinned by blocks.lock.json."""\n',
    )
    return RoleResult(
        ok=True,
        detail=f"vendored {len(vendored)} block(s)",
        vendored_blocks=tuple(vendored),
        notes={"lock": lock},
    )


# -- WRITER --------------------------------------------------------------

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
    """
    module = load_block(block_id)
    run = getattr(module, "run", None)
    if run is None:
        raise BlockNotVendored(f"{block_id} exposes no run() entry point")
    kwargs = dict(params or {})
    if action is not None:
        kwargs["action"] = action
    return run(input=payload, **kwargs)
'''


def _handler_module(
    capability_id: str,
    block_ids: Sequence[str],
    body: str,
    source: str,
    default_actions: Optional[Dict[str, str]] = None,
) -> str:
    return f'''"""Handler for capability {capability_id}.

Written by the factory WRITER role ({source}). Blocks are invoked through the
local dispatch runtime -- this module makes no network call.
"""

from __future__ import annotations

from typing import Any, Dict

from app.dispatch import execute

CAPABILITY_ID = "{capability_id}"
BLOCK_IDS = {list(block_ids)!r}
#: Each block's declared default action (from its block.json). Blocks are
#: action-dispatched; calling one with no action is answered with an error.
BLOCK_DEFAULT_ACTIONS = {dict(default_actions or {})!r}


def handle(payload: Dict[str, Any]) -> Dict[str, Any]:
{body}
'''


def _templated_body(block_ids: Sequence[str]) -> str:
    if not block_ids:
        return (
            '    return {"capability": CAPABILITY_ID, "status": "no_block_bound",\n'
            '            "detail": "no vendored block backs this capability"}'
        )
    return (
        "    results = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        results[block_id] = execute(\n"
        "            block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
        "        )\n"
        '    return {"capability": CAPABILITY_ID, "status": "ok", "results": results}'
    )


_PY_DEFAULTS = {"str": '""', "int": "0", "float": "0.0", "bool": "False"}
_SQL_TYPES = {"str": "TEXT", "int": "INTEGER", "float": "REAL", "bool": "INTEGER"}


def _fallback_spec(cap: Any) -> Dict[str, Any]:
    """Deterministic schema when no coder is available.

    Deliberately minimal and honest: a generic record envelope rather than a
    guessed domain model. The template does not pretend to domain knowledge it
    does not have.
    """
    return {
        "entity": cap.capability_id.replace("-", "_"),
        "fields": [
            {"name": "reference", "type": "str", "required": True},
            # A vocabulary and a bound on the deterministic path too, so the
            # constraint contract is exercised on a keyless CI run rather than
            # only when an agent happens to declare one.
            {
                "name": "status",
                "type": "str",
                "required": True,
                "allowed_values": ["open", "in_progress", "closed"],
            },
            {
                "name": "quantity",
                "type": "int",
                "required": False,
                "min": 0,
                "max": 10000,
            },
        ],
        "model": None,
    }


def _render_models(specs: Dict[str, Dict[str, Any]]) -> str:
    """dataclass per entity. Stdlib only, so the artifact needs no ORM."""
    out = [
        '"""Domain models for this platform.',
        "",
        "Plain dataclasses on purpose: the delivered platform must run with no",
        "ORM, no service, and no network. Persistence is in app/store.py.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import asdict, dataclass, field",
        "from typing import Any, Dict, Optional",
        "",
        "",
    ]
    for cap_id, spec in sorted(specs.items()):
        cls = "".join(p.title() for p in spec["entity"].split("_")) or "Record"
        out += [
            "@dataclass",
            f"class {cls}:",
            f'    """Entity for capability {cap_id}."""',
            "",
            "    id: Optional[int] = None",
        ]
        for f in spec["fields"]:
            out.append(f"    {f['name']}: {f['type']} = {_field_default(f)}")
        out += [
            "",
            "    FIELDS = " + repr([f["name"] for f in spec["fields"]]),
            # The single source of truth for value restrictions: the route
            # validates against these and the tests build payloads from them,
            # so neither side can invent a rule the other cannot satisfy.
            "    CONSTRAINTS = " + repr(_constraints_of(spec)),
            "",
            "    def to_dict(self) -> Dict[str, Any]:",
            "        return asdict(self)",
            "",
            "    @classmethod",
            "    def from_dict(cls, data: Dict[str, Any]) -> \"" + cls + "\":",
            "        known = {k: v for k, v in (data or {}).items() if k in cls.FIELDS}",
            "        return cls(id=(data or {}).get(\"id\"), **known)",
            "",
            "",
        ]
    out.append("MODELS = {")
    for cap_id, spec in sorted(specs.items()):
        cls = "".join(p.title() for p in spec["entity"].split("_")) or "Record"
        out.append(f'    "{cap_id}": {cls},')
    out.append("}")
    return "\n".join(out) + "\n"


def _render_store(specs: Dict[str, Dict[str, Any]]) -> str:
    """sqlite3 persistence derived from the same specs the models came from.

    stdlib sqlite3, file-backed, no server. One table per entity, columns
    generated from the spec so the schema cannot drift from the dataclass.
    """
    tables = []
    for spec in sorted(specs.values(), key=lambda s: s["entity"]):
        cols = ", ".join(
            f"{f['name']} {_SQL_TYPES[f['type']]}" for f in spec["fields"]
        )
        tables.append(
            f'    "{spec["entity"]}": "CREATE TABLE IF NOT EXISTS {spec["entity"]} '
            f'(id INTEGER PRIMARY KEY AUTOINCREMENT, {cols})",'
        )
    columns = {
        spec["entity"]: [f["name"] for f in spec["fields"]]
        for spec in specs.values()
    }
    return (
        '"""SQLite persistence for the domain models.\n'
        "\n"
        "stdlib sqlite3 and a local file: the platform stores data with no\n"
        "database server and no network. STORAGE_PATH relocates the file.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import sqlite3\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, List\n"
        "\n"
        "SCHEMA = {\n" + "\n".join(tables) + "\n}\n"
        "\n"
        f"COLUMNS: Dict[str, List[str]] = {columns!r}\n"
        "\n"
        "\n"
        "def db_path() -> Path:\n"
        '    root = Path(os.getenv("STORAGE_PATH", "./data"))\n'
        "    root.mkdir(parents=True, exist_ok=True)\n"
        '    return root / "platform.db"\n'
        "\n"
        "\n"
        "def connect() -> sqlite3.Connection:\n"
        "    conn = sqlite3.connect(db_path())\n"
        "    conn.row_factory = sqlite3.Row\n"
        "    for ddl in SCHEMA.values():\n"
        "        conn.execute(ddl)\n"
        "    conn.commit()\n"
        "    return conn\n"
        "\n"
        "\n"
        "def save(entity: str, record: Dict[str, Any]) -> Dict[str, Any]:\n"
        '    """Insert a record and return it with its assigned id."""\n'
        "    cols = COLUMNS[entity]\n"
        "    values = [record.get(c) for c in cols]\n"
        '    placeholders = ", ".join("?" for _ in cols)\n'
        "    conn = connect()\n"
        "    try:\n"
        "        cur = conn.execute(\n"
        '            f"INSERT INTO {entity} ({\', \'.join(cols)}) VALUES ({placeholders})",\n'
        "            values,\n"
        "        )\n"
        "        conn.commit()\n"
        '        return {"id": cur.lastrowid, **{c: record.get(c) for c in cols}}\n'
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def list_all(entity: str) -> List[Dict[str, Any]]:\n"
        "    conn = connect()\n"
        "    try:\n"
        '        rows = conn.execute(f"SELECT * FROM {entity} ORDER BY id").fetchall()\n'
        "        return [dict(r) for r in rows]\n"
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def get(entity: str, record_id: int) -> Dict[str, Any] | None:\n"
        "    conn = connect()\n"
        "    try:\n"
        "        row = conn.execute(\n"
        '            f"SELECT * FROM {entity} WHERE id = ?", (record_id,)\n'
        "        ).fetchone()\n"
        "        return dict(row) if row else None\n"
        "    finally:\n"
        "        conn.close()\n"
    )


def _templated_route_body(spec: Dict[str, Any]) -> str:
    """Deterministic endpoint that validates exactly the declared constraints.

    Enforcing them here too keeps the two paths honest against one contract:
    CI has no LLM key, so without this the constraint mechanism would only
    ever be exercised on a keyed run.
    """
    constraints = _constraints_of(spec)
    lines = [
        "    if not isinstance(payload, dict):",
        '        return {"ok": False, "error": "payload must be an object"}',
        f"    constraints = {constraints!r}",
        "    for name, rules in constraints.items():",
        "        if name not in payload:",
        "            continue",
        "        value = payload[name]",
        '        allowed = rules.get("allowed_values")',
        "        if allowed is not None and value not in allowed:",
        '            return {"ok": False,',
        '                    "error": name + " must be one of: " + ", ".join(allowed)}',
        '        low, high = rules.get("min"), rules.get("max")',
        "        if isinstance(value, bool) or not isinstance(value, (int, float)):",
        "            continue",
        "        if low is not None and value < low:",
        '            return {"ok": False, "error": name + " is below the minimum"}',
        "        if high is not None and value > high:",
        '            return {"ok": False, "error": name + " is above the maximum"}',
        "    result = handle(payload)",
        "    stored = save(payload)",
        '    return {"ok": True, "capability": CAPABILITY_ID, "result": result,',
        '            "stored": stored}',
    ]
    return "\n".join(lines)


def _render_routes(entries: List[Dict[str, Any]]) -> str:
    """FastAPI router: one POST and one GET per capability."""
    out = [
        '"""HTTP surface for the platform\'s capabilities.',
        "",
        "Every route runs entirely in-process: the handler dispatches to a",
        "vendored block and the result is persisted locally. No outbound call.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any, Dict",
        "",
        "from fastapi import APIRouter",
        "",
        "from app import store",
        "",
        "router = APIRouter()",
        "",
        "",
    ]
    for e in entries:
        name, entity = e["name"], e["entity"]
        out += [
            f"# --- {e['capability_id']} ({e['source']}) ---",
            f"from app.actions import {name} as _{name}_action  # noqa: E402",
            "",
            "",
            f"def _{name}_handle(payload: Dict[str, Any]) -> Dict[str, Any]:",
            f"    return _{name}_action.handle(payload)",
            "",
            "",
            f'@router.post("/{name}")',
            f"def {name}_create(payload: Dict[str, Any]) -> Dict[str, Any]:",
            f'    CAPABILITY_ID = "{e["capability_id"]}"',
            f"    handle = _{name}_handle",
            f'    save = lambda record: store.save("{entity}", record)',
            f'    list_all = lambda: store.list_all("{entity}")',
            e["body"],
            "",
            "",
            f'@router.get("/{name}")',
            f"def {name}_list() -> Dict[str, Any]:",
            f'    return {{"items": store.list_all("{entity}")}}',
            "",
            "",
        ]
    return "\n".join(out)


def _render_main(product_name: str) -> str:
    return (
        '"""Entrypoint for the generated platform.\n'
        "\n"
        "Runs standalone: uvicorn app.main:app. No factory, no block store, no\n"
        "outbound dependency at runtime.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from fastapi import FastAPI\n"
        "\n"
        "from app.routes import router\n"
        "\n"
        f'app = FastAPI(title="{product_name}")\n'
        'app.include_router(router, prefix="/v1")\n'
        "\n"
        "\n"
        '@app.get("/health")\n'
        "def health() -> dict:\n"
        '    return {"status": "ok"}\n'
    )


def _render_requirements() -> str:
    return (
        "# Runtime dependencies. Persistence is stdlib sqlite3 on purpose --\n"
        "# the platform runs with no database server and no network.\n"
        "fastapi>=0.110\n"
        "uvicorn>=0.29\n"
    )


# -- deploy scaffold ------------------------------------------------------
#
# Templated, not coder-written, and deliberately so. Container and process
# config is mechanical: there is no domain judgement for an agent to
# contribute, and a hallucinated base image or start command is a deployment
# failure rather than a test failure. The dual path exists for artifacts where
# the agent knows something the template cannot.


def _render_dockerfile() -> str:
    return (
        "# Standalone image. Blocks are vendored into the repository at build\n"
        "# time, so the container needs no block store and no outbound network\n"
        "# at runtime.\n"
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        "\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "\n"
        "COPY . .\n"
        "ENV PYTHONPATH=/app\n"
        "# Persistence is a sqlite file; mount a volume here to keep it.\n"
        "ENV STORAGE_PATH=/app/data\n"
        "RUN mkdir -p /app/data\n"
        "\n"
        "EXPOSE 8000\n"
        'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
    )


def _render_dockerignore() -> str:
    return (
        "__pycache__/\n"
        "*.py[cod]\n"
        ".pytest_cache/\n"
        "data/\n"
        ".env\n"
        "build_ledger.jsonl\n"
    )


def _render_procfile() -> str:
    return "web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}\n"


def _render_platform_env_example() -> str:
    """Note what is absent: there is no store URL and no store key.

    The template generator's .env.example documents CEREBRUM_API_URL because
    its handlers POST to the block store. This platform's handlers import the
    blocks vendored beside them, so a store variable here would be a lie about
    how it runs.
    """
    return (
        "# Copy to .env and fill in. Never commit real values.\n"
        "ENV=production\n"
        "\n"
        "# Where the sqlite file lives. Mount a volume at this path to persist.\n"
        "STORAGE_PATH=./data\n"
        "\n"
        "# Deliberately absent: there is no block-store URL and no store key\n"
        "# to set. Blocks are vendored under vendor/blocks/ and invoked\n"
        "# in-process via app/dispatch.py, so this platform makes no outbound\n"
        "# call at runtime and needs neither the factory nor the store to be\n"
        "# reachable. The omission is the design, not an oversight.\n"
    )


def _render_render_yaml(product_id: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(product_id).lower()).strip("-") or "platform"
    return (
        "# Render blueprint. One web service, no database and no key-value\n"
        "# store: persistence is a sqlite file on the mounted disk.\n"
        "services:\n"
        "  - type: web\n"
        f"    name: {slug}\n"
        "    runtime: docker\n"
        "    dockerfilePath: ./Dockerfile\n"
        "    healthCheckPath: /health\n"
        "    envVars:\n"
        "      - key: STORAGE_PATH\n"
        "        value: /app/data\n"
        "    disk:\n"
        f"      name: {slug}-data\n"
        "      mountPath: /app/data\n"
        "      sizeGB: 1\n"
    )


def _templated_readme(product_name: str, caps: Sequence[str], blocks: Sequence[str]) -> str:
    cap_lines = "\n".join(f"- `{c}`" for c in caps) or "- (none)"
    block_lines = "\n".join(f"- `{b}`" for b in blocks) or "- (none)"
    return f"""# {product_name}

Generated by the CerebrumDev factory role runner.

## What this is

A standalone platform. Every capability runs in-process: handlers invoke
blocks that were vendored into `vendor/blocks/` at build time, through
`app/dispatch.py`. There is **no call back to a block store at runtime** — the
platform runs with the factory switched off.

## Capabilities

{cap_lines}

## Vendored blocks

{block_lines}

## Run it

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # GET /health -> 200
python -m pytest tests             # the platform's own suite
```

Data lands in `$STORAGE_PATH/platform.db` (default `./data`), stdlib sqlite3.

## Layout

| Path | Purpose |
|---|---|
| `app/models.py` | domain dataclasses |
| `app/store.py` | sqlite persistence |
| `app/routes.py` | HTTP surface |
| `app/actions/` | capability handlers |
| `app/dispatch.py` | local block dispatch |
| `vendor/blocks/` | vendored block source, pinned by `blocks.lock.json` |
"""


def _block_contract(ctx: RoleContext, block_id: str) -> Dict[str, Any]:
    """What this vendored block actually accepts, gathered at build time.

    Two sources, both already in the workspace: the block.json the CLONER
    vendored (declared inputs, default action, action options) and -- for
    Store runtime blocks -- the ``input_schema`` required fields parsed from
    the vendored module. The first live build failed precisely here: handlers
    sent raw domain payloads to blocks that validate their input, and nothing
    told either the coder or the template what the blocks wanted.
    """
    contract: Dict[str, Any] = {"block_id": block_id}
    meta_rel = Path("vendor") / "blocks" / block_id / "block.json"
    if ctx.workspace.exists(meta_rel):
        try:
            meta = json.loads(ctx.workspace.read_text(meta_rel))
        except (ValueError, OSError):
            meta = {}
        declared = []
        for item in meta.get("inputs", []):
            name = item.get("name")
            if not name:
                continue
            if name == "action":
                if item.get("default") is not None:
                    contract["default_action"] = item["default"]
                if item.get("options"):
                    contract["action_options"] = list(item["options"])
                continue
            declared.append(
                {
                    "name": name,
                    "type": item.get("type"),
                    "required": bool(item.get("required")),
                }
            )
        if declared:
            contract["declared_inputs"] = declared

    module_rel = Path("vendor") / "cerebrum" / "blocks" / f"{block_id}.py"
    if ctx.workspace.exists(module_rel):
        source = ctx.workspace.read_text(module_rel)
        schema = re.search(
            r"input_schema\s*=\s*Schema\((.*?)\)", source, re.DOTALL
        )
        if schema:
            required = re.search(r"required_fields\s*=\s*\[([^\]]*)\]", schema.group(1))
            if required:
                fields = re.findall(r"[\"'](\w+)[\"']", required.group(1))
                if fields:
                    contract["input_required_fields"] = fields
    return contract


def _coder_body(ctx: RoleContext, cap: Any, usable: Sequence[str]) -> Optional[tuple]:
    """Ask the coding agent for this handler's body, or None if unavailable.

    Returns ``(body, source)``. None means the agent could not be used --
    disabled, unconfigured, or it failed its validation gate. The caller then
    ships the deterministic body and records *which* path produced it, so the
    artifact never implies authorship it did not have.
    """
    from app.factory.coder import CoderError, coder_enabled, generate_platform_handler

    if not coder_enabled():
        return None
    try:
        result = generate_platform_handler(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            block_ids=list(usable),
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
            work_list=list(ctx.work_list),
            block_contracts={b: _block_contract(ctx, b) for b in usable},
        )
    except CoderError as exc:
        # Degraded output is acceptable; invisible degradation is not.
        ctx.state.setdefault("coder_failures", {})[cap.capability_id] = str(exc)
        return None
    return result["body"], f"coder LLM ({result['model']})"


def _record_failure(ctx: RoleContext, key: str, exc: Exception) -> None:
    ctx.state.setdefault("coder_failures", {})[key] = str(exc)


def _coder_model_spec(ctx: RoleContext, cap: Any) -> Optional[Dict[str, Any]]:
    """Let the agent design the schema. None means fall back to the template."""
    from app.factory.coder import CoderError, coder_enabled, generate_model_spec

    if not coder_enabled():
        return None
    try:
        return generate_model_spec(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
        )
    except CoderError as exc:
        _record_failure(ctx, f"model:{cap.capability_id}", exc)
        return None


def _coder_route_body(
    ctx: RoleContext, cap: Any, spec: Dict[str, Any]
) -> Optional[tuple]:
    from app.factory.coder import CoderError, coder_enabled, generate_route_body

    if not coder_enabled():
        return None
    try:
        result = generate_route_body(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            entity=spec["entity"],
            fields=list(spec["fields"]),
            work_list=list(ctx.work_list),
        )
    except CoderError as exc:
        _record_failure(ctx, f"route:{cap.capability_id}", exc)
        return None
    return result["body"], f"coder LLM ({result['model']})"


def _coder_readme(
    ctx: RoleContext, product_name: str, caps: Sequence[str], blocks: Sequence[str]
) -> Optional[tuple]:
    """Prose is the one artifact where the template is nearly as good.

    Kept on the dual path anyway so every artifact class is consistent, but a
    failure here is uninteresting and silently templated.
    """
    from app.factory.coder import CoderError, coder_enabled

    if not coder_enabled():
        return None
    try:
        from app.factory.coder import _llm_code_call, get_factory_llm_config_model

        text = _llm_code_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Write a concise README.md for a generated business platform. "
                        "Markdown only, no code fences around the whole document. "
                        "Cover: what it does, its capabilities, how to run it "
                        "(pip install -r requirements.txt; uvicorn app.main:app), "
                        "and that it runs fully offline with blocks vendored into "
                        "vendor/blocks/ and no call back to any store."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Platform: {product_name}\n"
                        f"Capabilities: {list(caps)!r}\n"
                        f"Vendored blocks: {list(blocks)!r}"
                    ),
                },
            ]
        )
    except CoderError as exc:
        _record_failure(ctx, "readme", exc)
        return None
    if not text.strip():
        return None
    return text, f"coder LLM ({get_factory_llm_config_model()})"


def run_writer(ctx: RoleContext) -> RoleResult:
    """Manufacture the platform: dispatch runtime plus one handler per capability.

    The coding agent writes each body when one is configured; otherwise the
    body is composed from the block contract deterministically. Which path ran
    is stamped into every module header and reported in the result -- CI has
    no key and must still exercise this route, so the fallback is a first-class
    path rather than an error.

    On a rework pass ``ctx.work_list`` carries the TESTER's findings, which are
    handed to the coder as the thing to fix.
    """
    ctx.workspace.write_text(Path("app") / "__init__.py", '"""Generated platform."""\n')
    ctx.workspace.write_text(Path("app") / "dispatch.py", _DISPATCH_RUNTIME)

    vendored = set(ctx.state.get("vendored_blocks", ()))
    written: List[str] = []
    sources: Dict[str, str] = {}
    fallback_source = "deterministic contract template"

    actions_init = ['"""Capability handlers."""', ""]
    for cap in ctx.plan.capabilities:
        name = cap.capability_id.replace("-", "_")
        usable = [b for b in cap.block_ids if b in vendored]

        authored = _coder_body(ctx, cap, usable)
        body, source = authored or (_templated_body(usable), fallback_source)

        default_actions = {
            b: contract["default_action"]
            for b in usable
            if (contract := _block_contract(ctx, b)).get("default_action")
        }
        ctx.workspace.write_text(
            Path("app") / "actions" / f"{name}.py",
            _handler_module(
                cap.capability_id, usable, body, source, default_actions
            ),
        )
        actions_init.append(f"from app.actions import {name}  # noqa: F401")
        written.append(name)
        sources[cap.capability_id] = source

    ctx.workspace.write_text(
        Path("app") / "actions" / "__init__.py", "\n".join(actions_init) + "\n"
    )

    # --- domain models, designed by the agent when one is available --------
    specs: Dict[str, Dict[str, Any]] = {}
    for cap in ctx.plan.capabilities:
        spec = _coder_model_spec(ctx, cap) or _fallback_spec(cap)
        specs[cap.capability_id] = spec
        sources[f"model:{cap.capability_id}"] = (
            f"coder LLM ({spec['model']})" if spec.get("model") else fallback_source
        )
    ctx.workspace.write_text(Path("app") / "models.py", _render_models(specs))

    # Persistence is rendered from the same specs, so the schema cannot drift
    # from the dataclasses it stores.
    ctx.workspace.write_text(Path("app") / "store.py", _render_store(specs))
    sources["persistence"] = (
        "derived from coder-designed models"
        if any(s.get("model") for s in specs.values())
        else fallback_source
    )

    # --- API surface -------------------------------------------------------
    entries: List[Dict[str, Any]] = []
    for cap in ctx.plan.capabilities:
        spec = specs[cap.capability_id]
        authored = _coder_route_body(ctx, cap, spec)
        body, route_source = authored or (_templated_route_body(spec), fallback_source)
        entries.append(
            {
                "capability_id": cap.capability_id,
                "name": cap.capability_id.replace("-", "_"),
                "entity": spec["entity"],
                "body": body,
                "source": route_source,
            }
        )
        sources[f"route:{cap.capability_id}"] = route_source
    ctx.workspace.write_text(Path("app") / "routes.py", _render_routes(entries))

    # --- run scaffold ------------------------------------------------------
    product_name = getattr(ctx.blueprint, "product_name", "Generated Platform")
    ctx.workspace.write_text(Path("app") / "main.py", _render_main(product_name))
    ctx.workspace.write_text("requirements.txt", _render_requirements())
    readme = _coder_readme(ctx, product_name, written, sorted(vendored))
    ctx.workspace.write_text(
        "README.md",
        readme[0] if readme else _templated_readme(product_name, written, sorted(vendored)),
    )
    sources["readme"] = readme[1] if readme else fallback_source
    sources["entrypoint"] = fallback_source
    sources["requirements"] = fallback_source

    # Deploy scaffold: without these the artifact can only be run locally.
    product_id = getattr(ctx.blueprint, "product_id", "platform")
    ctx.workspace.write_text("Dockerfile", _render_dockerfile())
    ctx.workspace.write_text(".dockerignore", _render_dockerignore())
    ctx.workspace.write_text("Procfile", _render_procfile())
    ctx.workspace.write_text(".env.example", _render_platform_env_example())
    ctx.workspace.write_text("render.yaml", _render_render_yaml(product_id))
    sources["deploy_scaffold"] = fallback_source

    by_coder = sum(1 for s in sources.values() if s.startswith("coder LLM"))
    detail = (
        f"{len(written)} capability(ies); {len(sources)} artifact(s) — "
        f"{by_coder} by the coding agent, {len(sources) - by_coder} templated"
    )
    if ctx.work_list:
        detail += f"; reworking {len(ctx.work_list)} finding(s)"
    return RoleResult(
        ok=True,
        detail=detail,
        notes={
            "handlers": written,
            "artifact_sources": sources,
            "coder_artifacts": by_coder,
            "model_specs": specs,
        },
    )


# -- TESTER --------------------------------------------------------------


_SAMPLE_VALUES = {"str": "sample", "int": 1, "float": 1.5, "bool": True}


def _sample_value(field: Dict[str, Any]) -> Any:
    """A value that satisfies every constraint the field declares.

    Typing alone is not enough. An agent-designed field often carries a
    vocabulary or a range, and a generic "sample" is type-valid but
    domain-invalid -- the route rejects it, the gate fails, and the rework
    loop cannot recover because the only way for the writer to pass would be
    to delete its own validation.
    """
    if field.get("allowed_values"):
        return field["allowed_values"][0]
    ftype = field["type"]
    if ftype in ("int", "float"):
        lo, hi = field.get("min"), field.get("max")
        if lo is not None:
            return lo
        if hi is not None:
            return hi if hi < _SAMPLE_VALUES[ftype] else _SAMPLE_VALUES[ftype]
    return _SAMPLE_VALUES[ftype]


def _sample_payload(spec: Dict[str, Any]) -> Dict[str, Any]:
    """A valid instance of the entity, built from its own spec."""
    return {f["name"]: _sample_value(f) for f in spec.get("fields", [])}


def _field_default(field: Dict[str, Any]) -> str:
    """Python literal for the dataclass default, valid under the constraints."""
    if field.get("allowed_values"):
        return repr(field["allowed_values"][0])
    if field["type"] in ("int", "float") and field.get("min") is not None:
        return repr(field["min"])
    return _PY_DEFAULTS[field["type"]]


def _constraints_of(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for f in spec.get("fields", []):
        c = {k: f[k] for k in ("allowed_values", "min", "max") if f.get(k) is not None}
        if c:
            out[f["name"]] = c
    return out


_CONFTEST = '''"""Test bootstrap for the generated platform.

Puts the platform root on sys.path and points persistence at a scratch
directory, so running the suite never touches a real data file.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("STORAGE_PATH", tempfile.mkdtemp(prefix="platform-test-"))
'''


def run_tester(ctx: RoleContext) -> RoleResult:
    """Write tests that EXERCISE the platform. Writes only under tests/.

    Import-only tests are worthless here: they pass on a platform whose
    persistence is broken, whose routes 500, and whose blocks never run.
    These drive the real surface -- a model round-trips through sqlite, each
    route returns its documented shape, and every capability executes end to
    end through local dispatch with the store environment stripped.

    That last one is the assertion the old generated platforms shipped but
    could not honour, because their handlers called the store over HTTP.
    """
    caps = [c.capability_id.replace("-", "_") for c in ctx.plan.capabilities]
    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
    specs = ctx.state.get("model_specs") or {}
    entities = {
        cap.capability_id.replace("-", "_"): specs.get(cap.capability_id, {}).get(
            "entity", cap.capability_id.replace("-", "_")
        )
        for cap in ctx.plan.capabilities
    }

    ctx.workspace.write_text(Path("tests") / "conftest.py", _CONFTEST)

    # -- capability + offline dispatch ------------------------------------
    smoke = [
        '"""The platform runs, and it runs without the store."""',
        "",
        "import os",
        "",
        "",
        "def test_capabilities_import():",
    ]
    for name in caps:
        smoke.append(f"    from app.actions import {name}")
        smoke.append(f"    assert {name}.CAPABILITY_ID")
    if not caps:
        smoke.append("    pass")
    smoke += [
        "",
        "",
        "def test_dispatch_runs_offline():",
        '    """No store env, no network: blocks must run from vendor/."""',
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):',
        "        os.environ.pop(var, None)",
        "    from app.dispatch import execute",
        f"    for block_id in {vendored!r}:",
        "        result = execute(block_id, {'probe': True})",
        "        assert isinstance(result, dict), block_id",
        "",
        "",
        "def test_every_capability_executes_end_to_end():",
        '    """Each handler actually runs its blocks, not just imports."""',
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):',
        "        os.environ.pop(var, None)",
    ]
    for name in caps:
        smoke += [
            f"    from app.actions import {name}",
            f"    out = {name}.handle({{'reference': 'probe', 'status': 'new', 'quantity': 1,",
            "                          'data': {'product_id': 'p1', 'metrics': {}}})",
            f"    assert isinstance(out, dict), '{name} returned a non-dict'",
        ]
    if not caps:
        smoke.append("    pass")
    ctx.workspace.write_text(Path("tests") / "test_smoke.py", "\n".join(smoke) + "\n")

    # -- models round-trip through persistence -----------------------------
    model_lines = [
        '"""Models must survive a round trip through sqlite."""',
        "",
        "from app import store",
        "from app.models import MODELS",
        "",
        "",
        "def test_every_model_round_trips():",
    ]
    if entities:
        for cap_id, spec in sorted(specs.items()):
            entity = spec.get("entity", cap_id)
            sample = {
                f["name"]: {"str": "'x'", "int": "1", "float": "1.5", "bool": "True"}[
                    f["type"]
                ]
                for f in spec.get("fields", [])
            }
            payload = "{" + ", ".join(f"'{k}': {v}" for k, v in sample.items()) + "}"
            model_lines += [
                f"    record = {payload}",
                f"    saved = store.save('{entity}', record)",
                f"    assert saved['id'] is not None, 'no id assigned for {entity}'",
                f"    fetched = store.get('{entity}', saved['id'])",
                f"    assert fetched is not None, '{entity} did not persist'",
                "    for key, value in record.items():",
                "        assert fetched[key] == value, (key, fetched[key], value)",
                f"    assert any(r['id'] == saved['id'] for r in store.list_all('{entity}'))",
            ]
        model_lines += [
            "",
            "",
            "def test_models_expose_their_fields():",
            "    assert MODELS, 'no models were generated'",
            "    for cap_id, cls in MODELS.items():",
            "        instance = cls.from_dict({})",
            "        assert instance.to_dict()['id'] is None",
            "        assert cls.FIELDS, cap_id",
        ]
    else:
        model_lines.append("    pass")
    ctx.workspace.write_text(
        Path("tests") / "test_models.py", "\n".join(model_lines) + "\n"
    )

    # -- routes return their documented shape ------------------------------
    route_lines = [
        '"""The HTTP surface answers, and what it answers has the right shape."""',
        "",
        "from fastapi.testclient import TestClient",
        "",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        "",
        "",
        "def test_health():",
        '    resp = client.get("/health")',
        "    assert resp.status_code == 200",
        '    assert resp.json()["status"] == "ok"',
        "",
        "",
        "def test_every_capability_route_answers():",
    ]
    if caps:
        for cap in ctx.plan.capabilities:
            name = cap.capability_id.replace("-", "_")
            spec = specs.get(cap.capability_id, {})
            # A payload built from the entity's OWN fields. Posting an
            # arbitrary body would let a route that rejects everything pass:
            # it answers 200 with {"ok": false} and a shape-only assertion
            # cannot tell that apart from success.
            sample = _sample_payload(spec)
            route_lines += [
                f"    payload = {sample!r}",
                f'    resp = client.post("/v1/{name}", json=payload)',
                f'    assert resp.status_code == 200, ("{name}", resp.text)',
                "    body = resp.json()",
                f'    assert isinstance(body, dict), "{name} returned a non-object"',
                "    # A valid payload must not come back as an error.",
                '    assert body.get("ok") is not False, (',
                f'        "{name} rejected a payload built from its own schema: "',
                '        + str(body.get("error"))',
                "    )",
                "",
                f'    listed = client.get("/v1/{name}")',
                f'    assert listed.status_code == 200, ("{name} list", listed.text)',
                '    items = listed.json()["items"]',
                "    assert isinstance(items, list)",
                f'    assert items, "{name} accepted a record but persisted nothing"',
            ]
    else:
        route_lines.append("    pass")
    ctx.workspace.write_text(
        Path("tests") / "test_routes.py", "\n".join(route_lines) + "\n"
    )

    return RoleResult(
        ok=True,
        detail=(
            f"suite written for {len(caps)} capability(ies): dispatch, "
            "persistence round-trip, and route shape"
        ),
        notes={"capabilities": caps, "vendored": vendored, "entities": entities},
    )


# -- STORE_MANAGER -------------------------------------------------------


def run_store_manager(ctx: RoleContext) -> RoleResult:
    """Register what this build took from the Store.

    MINIMAL. This records the clone manifest for the registrar and applies no
    Store op. Harvesting improvements back upstream and admitting client-driven
    net-new capability into inventory are the unbuilt parts of this role --
    registered in KNOWN_INCOMPLETE.md rather than faked here.
    """
    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
    return RoleResult(
        ok=True,
        detail=f"registered {len(vendored)} clone(s); no store op applied",
        vendored_blocks=tuple(vendored),
        notes={"registered": vendored, "store_ops": []},
    )


ROLE_IMPLEMENTATIONS = {
    BuildRole.COLLECTOR: run_collector,
    BuildRole.CLONER: run_cloner,
    BuildRole.WRITER: run_writer,
    BuildRole.TESTER: run_tester,
    BuildRole.STORE_MANAGER: run_store_manager,
}
