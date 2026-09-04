"""Build-role implementations (collector, cloner, writer, tester, store manager).

Public types and templates live in ``roles_models`` / ``roles_constants``.
``roles.py`` re-exports this module so existing imports keep working.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.factory.build.authority import (
    KERNEL_ROUTE_NAMES,
    BuildRole,
    jobs_manifest,
    role_contract,
)
from app.factory.build.offline_adapters import (
    DOCUMENT_ENGINE_PARSERS_STUB,
    emit_instantiate_ready,
    emit_runtime_module,
    needs_document_engine_parsers_package,
)
from app.factory.build.block_inputs import (
    align_spec_to_handler_source,
    render_block_inputs_module,
    sanitize_python_identifier,
)
from app.factory.build.block_obligations import (
    BlockObligationError,
    assert_feedable,
    augment_model_spec,
    dependency_obligations,
    describe_resource_obligations,
    ensure_record_envelope,
    render_preconditions_module,
    render_dependency_lines,
)
from app.factory.build.roles_constants import (
    _BLOCK_CLASS_RE,
    _BLOCK_DEF_RE,
    _CONFTEST,
    _DISPATCH_RUNTIME,
    _GET_BLOCK_RE,
    _INSTANTIATE_HELPER,
    _PY_DEFAULTS,
    _REGISTRY_API_NAMES,
    _SAMPLE_VALUES,
    _STORE_FOREIGN_LAZY_RE,
    _STORE_FOREIGN_TOP_RE,
    _STORE_RUNTIME_RE,
)
from app.factory.build.roles_models import RoleContext, RoleError, RoleResult
from app.factory.build.supply_chain import (
    PYTHON_312_SLIM_FROM,
    SupplyChainError,
    assert_generated_dockerfile,
    emit_supply_chain_artifacts,
    redact_unpinned_images,
)
from app.factory.build.vendored_integrity import LOCK_KEY as _INTEGRITY_KEY
from app.factory.build.vendored_integrity import lock_record as _integrity_record


def _from_facade(name: str, fallback):
    """Resolve a helper via the ``roles.py`` facade.

    Tests monkeypatch ``app.factory.build.roles._block_source_dir``. Looking
    the name up here keeps that contract after the split.
    """
    import sys

    facade = sys.modules.get("app.factory.build.roles")
    if facade is None:
        return fallback
    return getattr(facade, name, fallback)


# -- COLLECTOR -----------------------------------------------------------


def run_collector(ctx: RoleContext) -> RoleResult:
    """Binding surveyor: resolve planned capabilities into parts, name every gap.

    Read-only by contract: this writes nothing. A capability the plan could
    not back with a block is reported as a gap so the WRITER knows it must
    author that logic -- silently dropping it is the failure mode the
    COLLECTOR gate exists to catch. The coding agent may review bindings;
    it may not change them. Published on the product as ``GET /v1/catalog``.
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

    reviews: List[Dict[str, Any]] = []
    review_model = ""
    try:
        reviews, review_model = _collector_agent_review(ctx)
    except Exception as exc:  # noqa: BLE001 -- review is advisory
        ctx.state.setdefault("coder_failures", {})["COLLECTOR.review"] = str(exc)
        ctx.note(f"coding agent skipped collector review: {exc}", stage="collector")

    mismatch_n = sum(1 for r in reviews if r.get("verdict") == "mismatch")
    detail = f"{len(seen)} block(s) resolved, {len(gaps)} gap(s)"
    if reviews:
        detail += (
            f"; coding agent reviewed {len(reviews)} binding(s)"
            f" ({mismatch_n} mismatch(es))"
        )
        ctx.note(
            f"coding agent reviewed collector bindings ({mismatch_n} mismatch(es))",
            stage="collector",
            reviews=len(reviews),
            mismatches=mismatch_n,
        )

    return RoleResult(
        ok=True,
        detail=detail,
        gaps=tuple(gaps),
        vendored_blocks=tuple(seen),
        notes={
            "resolved_blocks": list(seen),
            "gaps": gaps,
            "agent_binding_reviews": reviews,
            "agent_binding_model": review_model,
        },
    )


def _collector_block_meta(ctx: RoleContext, block_id: str) -> Dict[str, Any]:
    """Harvest a block.json the COLLECTOR can read (no vendor/ yet)."""
    meta: Dict[str, Any] = {"block_id": block_id}
    source = _from_facade("_block_source_dir", _block_source_dir)(
        block_id, ctx.blocks_root
    )
    if source is None:
        return meta
    path = source / "block.json"
    if not path.is_file():
        return meta
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return meta
    if isinstance(data, dict):
        if data.get("description"):
            meta["description"] = str(data["description"])[:400]
        actions = []
        for item in data.get("inputs") or []:
            if isinstance(item, dict) and item.get("name") == "action":
                if item.get("options"):
                    actions = list(item["options"])
                elif item.get("default") is not None:
                    actions = [item["default"]]
        if actions:
            meta["actions"] = actions
    return meta


def _collector_agent_review(ctx: RoleContext) -> tuple:
    """Ask the coding agent to judge bindings. Empty if unavailable."""
    from app.factory.coder import coder_enabled, review_capability_bindings

    if not coder_enabled():
        return [], ""
    if _budget_too_low(ctx, "collector review"):
        return [], ""
    caps = []
    for cap in ctx.plan.capabilities:
        bids = list(cap.block_ids or [])
        caps.append(
            {
                "id": cap.capability_id,
                "block_ids": bids,
                "description": getattr(cap, "notes", "") or cap.capability_id,
                "contracts": [_collector_block_meta(ctx, b) for b in bids],
            }
        )
    result = review_capability_bindings(
        product_name=getattr(ctx.blueprint, "product_name", "platform"),
        vertical=getattr(ctx.blueprint, "vertical", "product"),
        capabilities=caps,
    )
    return list(result.get("reviews") or []), str(result.get("model") or "")


# -- CLONER --------------------------------------------------------------


def _vendor_mirror_dir(block_id: str) -> Optional[Path]:
    mirror = Path(__file__).resolve().parents[1] / "vendor_blocks_mirror" / block_id
    if (mirror / "block.py").is_file():
        return mirror
    return None


def _block_source_dir(block_id: str, blocks_root: Optional[Path]) -> Optional[Path]:
    """Real Store checkout first, factory vendor mirror second."""
    if blocks_root:
        candidate = Path(blocks_root) / "block_registry" / block_id
        if (candidate / "block.py").is_file():
            return candidate
    return _vendor_mirror_dir(block_id)


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


def _rewrite_runtime_imports(text: str) -> str:
    text = re.sub(r"\bapp\.blocks\b", "vendor.cerebrum.blocks", text)
    return re.sub(r"\bapp\.core\b", "vendor.cerebrum.core", text)


def _rewrite_shim_constructors(text: str) -> str:
    """Live tasting-room CLONER shipped shims that do ``block_cls()``.

    DatabaseBlock.__init__ requires ``hal_block`` and ``config``. Instantiating
    with ``None`` then died on ``hal_block.cursor()`` in TESTER.
    """
    if not re.search(r"\bblock_cls\s*\(\s*\)", text):
        return text
    rewritten = re.sub(
        r"\bblock_cls\s*\(\s*\)",
        "_instantiate_store_block(block_cls)",
        text,
    )
    if "def _instantiate_store_block" not in rewritten:
        rewritten = _INSTANTIATE_HELPER.lstrip("\n") + "\n" + rewritten
    return rewritten


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


def _candidate_store_ids(block_id: str) -> tuple:
    """Kit-shelf ids and the Store's ``_v2`` runtime names.

    Dual-registration uses ``formula_executor``; the Store registry ships
    ``formula_executor_v2``. A live tasting-room Approve died in CLONER
    because the shim was found and the kit id was not in ``_EXTENDED_BLOCK_DEFS``.
    """
    ids = [block_id]
    if block_id.endswith("_v2") and len(block_id) > 3:
        ids.append(block_id[:-3])
    else:
        ids.append(f"{block_id}_v2")
    seen: Dict[str, None] = {}
    for item in ids:
        if item:
            seen.setdefault(item, None)
    return tuple(seen)


def _class_name_from_block_module(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    names = _BLOCK_CLASS_RE.findall(text)
    if not names:
        return None
    for name in names:
        if name.endswith("Block") or name.endswith("V2"):
            return name
    return names[0]


def _resolve_store_def(
    block_id: str, defs: Dict[str, tuple], blocks_root: Path
) -> Optional[tuple]:
    """``(store_key, (module, class))`` for a kit-shelf id, or None."""
    for key in _candidate_store_ids(block_id):
        if key in defs:
            return key, defs[key]
    blocks_dir = Path(blocks_root) / "app" / "blocks"
    for key in _candidate_store_ids(block_id):
        path = blocks_dir / f"{key}.py"
        if not path.is_file():
            continue
        cls = _class_name_from_block_module(path)
        if cls:
            return key, (key, cls)
    return None


def _runtime_defs_for_blocks(
    block_ids: Sequence[str],
    defs: Dict[str, tuple],
    blocks_root: Path,
    *,
    shim_texts: Optional[Dict[str, str]] = None,
) -> Dict[str, tuple]:
    """Vendored registry entries keyed by every name a shim may ``get_block``.

    The Store's native id (often ``*_v2``) and the kit-shelf id both point at
    the same module so ``get_block("formula_executor")`` resolves after clone.
    """
    registry: Dict[str, tuple] = {}
    init = Path(blocks_root) / "app" / "blocks" / "__init__.py"
    for bid in block_ids:
        resolved = _resolve_store_def(bid, defs, blocks_root)
        if resolved is None:
            tried = ", ".join(
                key for key in _candidate_store_ids(bid) if key != bid
            )
            extra = f" (also tried {tried})" if tried else ""
            raise RoleError(
                f"{bid}: shim imports the Store runtime but the Store registry "
                f"({init}) has no entry for it{extra}"
            )
        store_key, pair = resolved
        registry[bid] = pair
        registry[store_key] = pair
        if shim_texts and bid in shim_texts:
            for name in _GET_BLOCK_RE.findall(shim_texts[bid]):
                registry.setdefault(name, pair)
    return registry


def _shim_needs_runtime(source: Path) -> bool:
    return any(
        _STORE_RUNTIME_RE.search(p.read_text(encoding="utf-8", errors="replace"))
        for p in source.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _package_source_blob(pkg_dir: Path) -> str:
    parts: List[str] = []
    for py in sorted(pkg_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        parts.append(py.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _shadow_stem(mod: str) -> Optional[str]:
    """``document_engine_block`` → ``document_engine``; else None."""
    suffix = "_block"
    if not mod.endswith(suffix):
        return None
    stem = mod[: -len(suffix)]
    return stem or None


def _package_declares_sibling_wrapper(pkg_dir: Path, stem: str, synthetic_mod: Optional[str] = None) -> bool:
    """True when a shadowing package load-names the sibling ``{stem}.py``.

    The live Cerebrum-Blocks pattern (``document_engine/`` +
    ``document_engine.py``) sets ``_BLOCK_MODULE_NAME =
    "app.blocks.document_engine_block"`` and importlib-loads
    ``../document_engine.py``. Both tokens must be present so we do not
    invent a module from a coincidental filename mention.
    """
    if not (pkg_dir / "__init__.py").is_file():
        return False
    blob = _package_source_blob(pkg_dir)
    if f"{stem}.py" not in blob:
        return False
    if synthetic_mod is not None and f"app.blocks.{synthetic_mod}" not in blob:
        return False
    return True


def _resolve_shadowed_wrapper(blocks_dir: Path, mod: str) -> Optional[Path]:
    """Sibling ``{stem}.py`` a package importlib-loads as ``app.blocks.{mod}``.

    Live sess_000f9a85d339422f: CLONER scanned the ``document_engine/``
    package, regex-picked ``document_engine_block``, and failed closed
    because that path is not on disk. The wrapper lives at
    ``app/blocks/document_engine.py``. Map the synthetic name only when
    the package declares it and the sibling file exists.
    """
    if (blocks_dir / f"{mod}.py").is_file():
        return None
    if (blocks_dir / mod / "__init__.py").is_file():
        return None
    stem = _shadow_stem(mod)
    if stem is None:
        return None
    wrapper = blocks_dir / f"{stem}.py"
    pkg = blocks_dir / stem
    if not wrapper.is_file():
        return None
    if not _package_declares_sibling_wrapper(pkg, stem, mod):
        return None
    return wrapper


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
        resolved = _resolve_store_def(bid, defs, blocks_root)
        if resolved is None:
            tried = ", ".join(
                key for key in _candidate_store_ids(bid) if key != bid
            )
            extra = f" (also tried {tried})" if tried else ""
            raise RoleError(
                f"{bid}: shim imports the Store runtime but the Store registry "
                f"({blocks_dir / '__init__.py'}) has no entry for it{extra}"
            )
        _store_key, (mod, _cls) = resolved
        todo.append(mod)

    while todo:
        mod = todo.pop()
        if mod in block_mods:
            continue
        if "." in mod:
            raise RoleError(
                f"runtime slice cannot vendor app.blocks.{mod}: dotted "
                "registry module names are not supported by the slice "
                "vendorer yet (package *contents* are copied separately)"
            )
        path = blocks_dir / f"{mod}.py"
        pkg_init = blocks_dir / mod / "__init__.py"
        shadow = _resolve_shadowed_wrapper(blocks_dir, mod)
        sources: List[str] = []
        if pkg_init.is_file():
            for py in sorted((blocks_dir / mod).rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                sources.append(py.read_text(encoding="utf-8", errors="replace"))
        elif path.is_file():
            sources.append(path.read_text(encoding="utf-8", errors="replace"))
        elif shadow is not None:
            sources.append(shadow.read_text(encoding="utf-8", errors="replace"))
        else:
            raise RoleError(
                f"runtime slice needs app/blocks/{mod}.py or "
                f"app/blocks/{mod}/__init__.py which does not exist in "
                "the Store checkout"
            )
        block_mods[mod] = None
        for text in sources:
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
                            f"app/blocks/{mod} imports {name} from app.blocks "
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
    # Every vendored file's final text, keyed by workspace path. The
    # dependency obligation is read off THIS -- the bytes that ship -- not
    # off the Store checkout, which the rewrites have already diverged from.
    shipped: Dict[str, str] = {}

    def _write(rel: Path, text: str) -> None:
        lazy_foreign.extend(_check_foreign_app_imports(rel.as_posix(), text))
        ctx.workspace.write_text(rel, text)
        written.append(rel.as_posix())
        shipped[rel.as_posix()] = text

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

    shim_texts: Dict[str, str] = {}
    for bid in runtime_block_ids:
        shim = ctx.workspace.workspace / "vendor" / "blocks" / bid / "block.py"
        if shim.is_file():
            shim_texts[bid] = shim.read_text(encoding="utf-8", errors="replace")
    registry = _runtime_defs_for_blocks(
        runtime_block_ids, defs, blocks_root, shim_texts=shim_texts
    )
    _write(base / "blocks" / "__init__.py", _render_vendored_registry(registry))
    blocks_dir = blocks_root / "app" / "blocks"
    for mod in sorted(block_mods):
        pkg_src = blocks_dir / mod
        py_file = blocks_dir / f"{mod}.py"
        if (pkg_src / "__init__.py").is_file():
            for py in sorted(pkg_src.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                rel_inner = py.relative_to(pkg_src)
                source = py.read_text(encoding="utf-8", errors="replace")
                _write(
                    base / "blocks" / mod / rel_inner,
                    emit_runtime_module(mod, _rewrite_runtime_imports(source)),
                )
            parsers_init = (
                ctx.workspace.workspace / base / "blocks" / mod / "parsers" / "__init__.py"
            )
            if mod == "document_engine" and not parsers_init.is_file():
                joined = "\n".join(shipped.get(p, "") for p in written)
                if needs_document_engine_parsers_package(joined):
                    _write(
                        base / "blocks" / mod / "parsers" / "__init__.py",
                        DOCUMENT_ENGINE_PARSERS_STUB,
                    )
            sibling = blocks_dir / f"{mod}.py"
            if sibling.is_file() and _package_declares_sibling_wrapper(pkg_src, mod):
                # Keep ../{mod}.py next to the package so importlib load of
                # the shadowed wrapper still resolves after rewrite.
                source = sibling.read_text(encoding="utf-8", errors="replace")
                _write(
                    base / "blocks" / f"{mod}.py",
                    emit_runtime_module(mod, _rewrite_runtime_imports(source)),
                )
            continue
        if not py_file.is_file():
            shadow = _resolve_shadowed_wrapper(blocks_dir, mod)
            if shadow is None:
                raise RoleError(
                    f"runtime slice needs app/blocks/{mod}.py or "
                    f"app/blocks/{mod}/__init__.py which does not exist in "
                    "the Store checkout"
                )
            emit_mod = _shadow_stem(mod) or mod
            source = shadow.read_text(encoding="utf-8", errors="replace")
            _write(
                base / "blocks" / f"{mod}.py",
                emit_runtime_module(emit_mod, _rewrite_runtime_imports(source)),
            )
            continue
        source = py_file.read_text(encoding="utf-8", errors="replace")
        rewritten = emit_runtime_module(mod, _rewrite_runtime_imports(source))
        if needs_document_engine_parsers_package(rewritten):
            # A module file cannot host a .parsers submodule. Convert to a
            # package so ``vendor.cerebrum.blocks.document_engine.parsers``
            # imports (live sess_a69c8ce).
            _write(base / "blocks" / mod / "__init__.py", rewritten)
            _write(
                base / "blocks" / mod / "parsers" / "__init__.py",
                DOCUMENT_ENGINE_PARSERS_STUB,
            )
        else:
            _write(base / "blocks" / f"{mod}.py", rewritten)

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
            text = emit_instantiate_ready(_rewrite_shim_constructors(text))
            lazy_foreign.extend(_check_foreign_app_imports(rel.as_posix(), text))
            ctx.workspace.write_text(rel, text)
            shipped[rel.as_posix()] = text

    # Raises on an import with no recorded distribution. Failing the CLONER
    # is the point: a requirements.txt that omits what the source imports is
    # the same F10 defect as an import satisfied by accident, and it surfaces
    # as a dead feature in the customer's platform rather than a build error.
    ctx.state["vendored_dependencies"] = dependency_obligations(shipped)

    return written, sorted(set(lazy_foreign))


def _runtime_pin(blocks_root: Path) -> str:
    from app.factory.generator import git_head

    revision = git_head(Path(blocks_root))
    if revision and revision != "unknown":
        return revision
    return _content_digest(Path(blocks_root) / "app")


def run_cloner(ctx: RoleContext) -> RoleResult:
    """Block stocker: vendor each resolved block's real source into the workspace.

    Writes under ``vendor/``, ``kits/``, and the lockfile -- its whole lane.
    The lockfile records where each block (and kit pack) came from so the
    Store registrar can later tell a Store-sourced clone from a mirror stub.
    No agent. Published on the product as ``GET /v1/inventory``.
    """
    block_ids = tuple(ctx.state.get("resolved_blocks", ()))
    if not block_ids:
        # A plan of pure-GENERATE capabilities is legitimate; there is simply
        # nothing to vendor. Say so rather than inventing an empty lockfile.
        ctx.note("no blocks to vendor", stage="blocks", done=0, total=0)
        return RoleResult(ok=True, detail="no blocks to vendor", vendored_blocks=())

    lock: Dict[str, Any] = {"schema": "blocks.lock.v1", "blocks": {}}
    vendored: List[str] = []
    missing: List[str] = []
    runtime_blocks: List[str] = []
    defs = _store_block_defs(Path(ctx.blocks_root)) if ctx.blocks_root else {}
    ctx.note(
        f"cloning {len(block_ids)} block(s)",
        stage="blocks",
        done=0,
        total=len(block_ids),
    )

    for bid in block_ids:
        source = _from_facade("_block_source_dir", _block_source_dir)(
            bid, ctx.blocks_root
        )
        if source is None:
            missing.append(bid)
            continue
        needs_rt = _shim_needs_runtime(source)
        if needs_rt and ctx.blocks_root:
            if _resolve_store_def(bid, defs, Path(ctx.blocks_root)) is None:
                mirror = _vendor_mirror_dir(bid)
                if mirror is not None and not _shim_needs_runtime(mirror):
                    # Kit-shelf shim exists in Blocks but the Store runtime
                    # never registered it (and no ``_v2`` alias). Shipping
                    # that shim fails CLONER; the factory stub runs offline.
                    source = mirror
                    needs_rt = False
        ctx.workspace.copy_tree(source, Path("vendor") / "blocks" / bid)
        from app.factory.build.network_posture import apply_p1_cloned_block

        if apply_p1_cloned_block(ctx.workspace, bid):
            # P1 capture adapter has no Store shim; do not demand Blocks runtime.
            needs_rt = _shim_needs_runtime(
                ctx.workspace.workspace / "vendor" / "blocks" / bid
            )
        origin, revision = _pin_source(source, ctx.blocks_root)
        cloned_meta = ctx.workspace.workspace / "vendor" / "blocks" / bid / "block.json"
        image_pin = "none"
        if cloned_meta.is_file() and redact_unpinned_images(cloned_meta):
            image_pin = "refused_unverified"
        # Verify against the SOURCE, before the runtime-import rewrites below
        # change the bytes. Hashing the vendored copy would compare a
        # transformed file with an upstream digest and fail every build.
        integrity = _integrity_record(source)
        lock["blocks"][bid] = {
            "source": origin,
            "commit": revision,
            "path": f"vendor/blocks/{bid}",
            "image_pin": image_pin,
            _INTEGRITY_KEY: integrity,
        }
        vendored.append(bid)
        ctx.note(
            f"cloned {bid}",
            stage="blocks",
            done=len(vendored),
            total=len(block_ids),
            block_id=bid,
        )
        # A shim that reaches for the Store's runtime cannot import offline
        # on its own -- the slice it stands on must be vendored with it.
        #
        # Keyed on what the SOURCE NEEDS, never on where it came from. This
        # was gated on ``ctx.blocks_root`` and the factory's own vendor
        # mirror turned out to contain real Store shims (audit/, capture/),
        # so a build with no Store checkout vendored a shim that imports
        # app.blocks and shipped no runtime for it: the CLONER gate failed
        # with "No module named 'app'" on the production default path.
        if needs_rt:
            runtime_blocks.append(bid)

    if missing:
        raise RoleError(
            "no source found for block(s) — do not invent: "
            + ", ".join(sorted(missing))
        )

    if runtime_blocks and not ctx.blocks_root:
        # The slice can only be taken from a Store checkout. Vendoring the
        # shim without it would pass this role and fail the gate with an
        # opaque import error, so refuse here and name the fix.
        raise RoleError(
            "block(s) "
            + ", ".join(sorted(runtime_blocks))
            + " are Store shims that need the app.blocks/app.core runtime, but "
            "no Store checkout is available to vendor it from. Set "
            "CEREBRUM_BLOCKS_ROOT (or make CEREBRUM_BLOCKS_REPO cloneable) so "
            "the platform can carry the runtime it depends on."
        )

    if runtime_blocks:
        try:
            runtime_files, lazy_foreign = _vendor_runtime_slice(ctx, runtime_blocks)
        except BlockObligationError as exc:
            # Fail-closed is unchanged: an unknown import still refuses the
            # build. Convert to RoleError so RoleRunner records RUN_FAILED
            # with the missing module instead of the thread dying as
            # "build thread crashed" (live sess_d1cb9d51c5354bea /
            # CEREBRUMDEV-BACKEND-A).
            raise RoleError(str(exc)) from exc
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

    from app.factory.kit_pack import stock_kits_via_workspace

    kit_lock = stock_kits_via_workspace(
        ctx.workspace, vendored, blocks_root=ctx.blocks_root
    )
    if kit_lock:
        lock["kits"] = kit_lock
        ctx.workspace.write_text(
            "blocks.lock.json", json.dumps(lock, indent=2, sort_keys=True) + "\n"
        )

    return RoleResult(
        ok=True,
        detail=f"vendored {len(vendored)} block(s), {len(kit_lock)} kit(s)",
        vendored_blocks=tuple(vendored),
        notes={"lock": lock},
    )


# -- WRITER --------------------------------------------------------------


def _render_dispatch(contracts: Optional[Dict[str, Any]] = None) -> str:
    """Dispatch runtime with this build's harvested block contracts baked in."""
    baked = repr(dict(contracts or {}))
    return _DISPATCH_RUNTIME.replace(
        "BLOCK_CONTRACTS: Dict[str, Any] = {}",
        "BLOCK_CONTRACTS: Dict[str, Any] = " + baked,
        1,
    )


def _indent_handler_body(body: str, extra: int = 4) -> str:
    """Indent a handle() body so it can sit inside a nested ``_impl``."""
    pad = " " * extra
    return "\n".join((pad + line if line.strip() else line) for line in body.splitlines())


def _ensure_handler_fails_closed(body: str) -> str:
    """Refuse ``ok: True`` when ``execute()`` returned a block error.

    Coder handlers (moonshot/kimi on the live invoice-management run) often
    ignore the envelope and return success anyway. The deterministic template
    already fail-closes; this wrapper makes every path — coder or template —
    refuse success when a block failed. Idempotent: a body that already
    returns ``ok: False`` is left alone.

    Before the call, domain records are shaped into block-acceptable inputs
    via ``prepare_block_input`` (notification channel/message, workflow
    steps, team minted ``team_id``, document_engine existing file paths,
    event_bus ``topic``, database ``table``/``sql``, analytics
    metric/value). That is the residential-lettings + veterinary-care
    pilot fix: handlers that forward the domain JSON unchanged still
    reach the block with a contract-valid payload. Soft import keeps unit
    tests that stub only ``app.dispatch`` working.

    ``action`` is lifted out of the payload and passed only as the
    ``action=`` keyword (live makerspace-management, sess_39b5fec2abd346a5:
    every capability buried the operation in the dict; dispatch answered
    ``Unknown action: None`` / ``unknown field(s): action``).
    """
    return (
        "    import app.dispatch as _dispatch\n"
        "    try:\n"
        "        from app.block_inputs import prepare_block_input as _prepare_block_input\n"
        "    except ImportError:  # pragma: no cover - unit stubs without the module\n"
        "        def _prepare_block_input(block_id, data, **_kw):\n"
        "            return data if isinstance(data, dict) else {'value': data}\n"
        "    try:\n"
        "        from app.block_inputs import split_execute_action as _split_execute_action\n"
        "    except ImportError:  # pragma: no cover - unit stubs / older emit\n"
        "        def _split_execute_action(payload, action=None, default_action=None):\n"
        "            data = dict(payload) if isinstance(payload, dict) else (\n"
        "                {} if payload is None else {'value': payload}\n"
        "            )\n"
        "            inner = data.get('input') if isinstance(data.get('input'), dict) else {}\n"
        "            resolved = action\n"
        "            if not (isinstance(resolved, str) and resolved.strip()):\n"
        "                for cand in (data.get('action'), inner.get('action'), default_action):\n"
        "                    if isinstance(cand, str) and cand.strip():\n"
        "                        resolved = cand\n"
        "                        break\n"
        "                else:\n"
        "                    resolved = default_action\n"
        "            data.pop('action', None)\n"
        "            if isinstance(data.get('input'), dict):\n"
        "                data['input'] = dict(data['input'])\n"
        "                data['input'].pop('action', None)\n"
        "            return resolved, data\n"
        "    _block_errors = []\n"
        "    def _watched(block_id, *a, **kw):\n"
        "        data = a[0] if a else kw.get('payload', {})\n"
        "        action = kw.get('action')\n"
        "        if action is None and len(a) > 1:\n"
        "            action = a[1]\n"
        "        params = kw.get('params')\n"
        "        if params is None and len(a) > 2:\n"
        "            params = a[2]\n"
        "        action, data = _split_execute_action(\n"
        "            data,\n"
        "            action=action,\n"
        "            default_action=BLOCK_DEFAULT_ACTIONS.get(block_id),\n"
        "        )\n"
        "        prepared = _prepare_block_input(\n"
        "            block_id, data, action=action, roster=BLOCK_IDS,\n"
        "            entity=ENTITY,\n"
        "        )\n"
        "        if isinstance(prepared, dict):\n"
        "            prepared = dict(prepared)\n"
        "            prepared.pop('action', None)\n"
        "            if isinstance(prepared.get('input'), dict):\n"
        "                prepared['input'] = dict(prepared['input'])\n"
        "                prepared['input'].pop('action', None)\n"
        "        res = _dispatch.execute(\n"
        "            block_id, prepared, action=action, params=params\n"
        "        )\n"
        "        if isinstance(res, dict) and (\n"
        '            res.get("status") == "error" or "error" in res\n'
        "        ):\n"
        "            _block_errors.append(\n"
        '                "%s: %s" % (block_id, str(res.get("error") or res.get("status"))[:160])\n'
        "            )\n"
        "        return res\n"
        "    def _impl(payload, execute=_watched):\n"
        f"{_indent_handler_body(body, 4)}\n"
        "    result = _impl(payload)\n"
        "    if _block_errors and (\n"
        '        not isinstance(result, dict) or result.get("ok") is not False\n'
        "    ):\n"
        "        return {\n"
        '            "ok": False,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "error": "block failed: " + "; ".join(_block_errors),\n'
        '            "result": result,\n'
        "        }\n"
        "    return result"
    )


def _handler_module(
    capability_id: str,
    block_ids: Sequence[str],
    body: str,
    source: str,
    default_actions: Optional[Dict[str, str]] = None,
    field_names: Optional[Sequence[str]] = None,
    entity: Optional[str] = None,
) -> str:
    entity_name = entity or str(capability_id or "record").replace("-", "_")
    return f'''"""Handler for capability {capability_id}.

Written by the factory WRITER role ({source}). Blocks are invoked through the
local dispatch runtime -- this module makes no network call.
"""

from __future__ import annotations

from typing import Any, Dict

from app.dispatch import execute

CAPABILITY_ID = "{capability_id}"
ENTITY = {entity_name!r}
BLOCK_IDS = {list(block_ids)!r}
#: Each block's declared default action (from its block.json). Blocks are
#: action-dispatched; calling one with no action is answered with an error.
BLOCK_DEFAULT_ACTIONS = {dict(default_actions or {})!r}
#: This capability's own domain columns. The kernel strips trust-scope keys
#: from caller arguments; a column that shares one of those names (project_id
#: is the obvious one for construction) would be deleted before handle() ever
#: saw it. Declaring the names here tells the kernel they are domain data.
CAPABILITY_FIELDS = {list(field_names or [])!r}


def handle(payload: Dict[str, Any]) -> Dict[str, Any]:
{_ensure_handler_fails_closed(body)}
'''


def _templated_body(block_ids: Sequence[str]) -> str:
    if not block_ids:
        return (
            '    return {"capability": CAPABILITY_ID, "status": "no_block_bound",\n'
            '            "detail": "no vendored block backs this capability"}'
        )
    # Domain JSON is not block-acceptable JSON. prepare_block_input (invoked
    # inside the fail-closed execute wrapper, and explicitly here so the
    # template documents the contract) builds channel/message, steps, team
    # minted team_id, document file paths, event_bus topic, and database
    # table/sql from the capability record.
    # The nested check is the honesty line: the ninth live build shipped
    # three capabilities whose block calls all failed while the handler
    # reported ok -- the suite passed on a payload the blocks had rejected.
    return (
        "    results = {}\n"
        "    errors = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        result = execute(\n"
        "            block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
        "        )\n"
        "        results[block_id] = result\n"
        "        if isinstance(result, dict) and (\n"
        '            result.get("status") == "error" or "error" in result\n'
        "        ):\n"
        '            errors[block_id] = str(result.get("error") or result)[:200]\n'
        "    if errors:\n"
        "        return {\n"
        '            "ok": False,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "error": "; ".join(f"{b}: {e}" for b, e in sorted(errors.items())),\n'
        '            "results": results,\n'
        "        }\n"
        '    return {"ok": True, "capability": CAPABILITY_ID, "results": results}'
    )


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


def _entity_class_name(entity: str) -> str:
    """PascalCase class name that is always a valid Python identifier."""
    raw = re.sub(r"[^0-9A-Za-z_]+", "_", str(entity or "record"))
    cls = "".join(p.title() for p in raw.split("_") if p) or "Record"
    if cls[0].isdigit() or not cls.isidentifier():
        cls = f"Record{cls}" if cls and cls[0].isdigit() else "Record"
    return cls


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
    class_names: Dict[str, str] = {}
    used_classes: set[str] = set()
    for cap_id, spec in sorted(specs.items()):
        cls = _entity_class_name(spec["entity"])
        if cls in used_classes:
            cls = sanitize_python_identifier(cls, used=used_classes)
        else:
            used_classes.add(cls)
        class_names[cap_id] = cls
        reserved = {
            "id",
            "self",
            "FIELDS",
            "CONSTRAINTS",
            "_FIELD_PY",
            "_FIELD_JSON",
            "to_dict",
            "from_dict",
        }
        rendered: List[tuple[Dict[str, Any], str]] = []
        for f in spec["fields"]:
            original = str(f.get("name") or "").strip()
            attr = sanitize_python_identifier(original, used=reserved)
            rendered.append((f, attr))
        json_to_py = {
            str(f["name"]): attr
            for f, attr in rendered
            if str(f.get("name") or "") != attr
        }
        py_to_json = {attr: str(f["name"]) for f, attr in rendered if str(f.get("name") or "") != attr}
        out += [
            "@dataclass",
            f"class {cls}:",
            f'    """Entity for capability {cap_id}."""',
            "",
            "    id: Optional[int] = None",
        ]
        for f, attr in rendered:
            out.append(
                f"    {attr}: {_python_annotation(f)} = {_field_default(f)}"
            )
        out += [
            "",
            "    FIELDS = " + repr([f["name"] for f in spec["fields"]]),
            # The single source of truth for value restrictions: the route
            # validates against these and the tests build payloads from them,
            # so neither side can invent a rule the other cannot satisfy.
            "    CONSTRAINTS = " + repr(_constraints_of(spec)),
            "    _FIELD_PY = " + repr(json_to_py),
            "    _FIELD_JSON = " + repr(py_to_json),
            "",
            "    def to_dict(self) -> Dict[str, Any]:",
            "        raw = asdict(self)",
            "        return {self._FIELD_JSON.get(k, k): v for k, v in raw.items()}",
            "",
            "    @classmethod",
            "    def from_dict(cls, data: Dict[str, Any]) -> \"" + cls + "\":",
            "        known = {}",
            "        for k, v in (data or {}).items():",
            "            if k == \"id\":",
            "                continue",
            "            attr = cls._FIELD_PY.get(k, k)",
            "            if k in cls.FIELDS or attr in cls._FIELD_JSON:",
            "                known[attr] = v",
            "        return cls(id=(data or {}).get(\"id\"), **known)",
            "",
            "",
        ]
    out.append("MODELS = {")
    for cap_id in sorted(specs):
        out.append(f'    "{cap_id}": {class_names[cap_id]},')
    out.append("}")
    return "\n".join(out) + "\n"


def _render_store(specs: Dict[str, Dict[str, Any]]) -> str:
    """sqlite3 persistence derived from the same specs the models came from.

    Schema is versioned Alembic (S10). connect() does not CREATE TABLE.
    """
    from app.factory.build.data_lifecycle import render_store

    return render_store(specs)


def _constraint_guard(spec: Dict[str, Any]) -> str:
    """Reject payloads that violate the capability's own field rules."""
    constraints = _constraints_of(spec)
    required = [
        str(f["name"])
        for f in (spec.get("fields") or [])
        if isinstance(f, dict) and f.get("name") and f.get("required")
    ]
    return "\n".join(
        [
            "    if not isinstance(payload, dict):",
            '        return {"ok": False, "error": "payload must be an object"}',
            f"    required_fields = {required!r}",
            "    for name in required_fields:",
            "        if name not in payload or payload[name] in (None, ''):",
            '            return {"ok": False,',
            '                    "error": "Missing required field: " + name}',
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
        ]
    )


def _templated_route_body(spec: Dict[str, Any]) -> str:
    """Capability POST routed through ``execute_action``. Persist stays here.

    This is not a new persist-wrapper. The kernel already owns trust-scope,
    input/output validation, and ActionResult. The route persists the request
    payload only after ActionStatus.SUCCESS, using the existing store.
    """
    lines = [
        _constraint_guard(spec),
        "    result = await run_capability(CAPABILITY_ID, payload)",
        "    if isinstance(result, dict) and result.get('status') != 'success':",
        "        return {'ok': False,",
        "                'error': result.get('error_message') or result.get('status'),",
        "                'result': result}",
        "    stored = save(payload)",
        '    return {"ok": True, "capability": CAPABILITY_ID, "result": result,',
        '            "stored": stored}',
    ]
    return "\n".join(lines)


def _ensure_route_persists_payload(body: str) -> str:
    """Coder routes often persist handle()'s envelope, not the request.

    The live winery-hospitality zip saved ``result`` / ``handled``
    (``{ok, data: {...}}``) into sqlite, so every column was NULL even
    though POST returned ``ok: true``. The record is the payload.
    """
    rewritten = re.sub(
        r"\bsave\(\s*(?!payload\s*\))([A-Za-z_][\w]*)\s*\)",
        "save(payload)",
        body,
    )
    if "save(payload)" not in rewritten:
        rewritten = rewritten.rstrip() + "\n    stored = save(payload)\n"
    return rewritten


def _render_jobs_module(
    *,
    catalog: Dict[str, Any],
    capabilities: List[Dict[str, Any]],
    gates: Dict[str, Any],
) -> str:
    """Frozen kernel JDs plus live readers for lock/provenance files."""
    jobs = jobs_manifest()
    return (
        '"""Kernel job descriptions shipped with this platform.\n'
        "\n"
        "Frozen at manufacture time from Factory RoleContract. HTTP routes\n"
        "publish each kernel's job; they never re-run that job. Inventory and\n"
        "provenance read lock/provenance files live so the register stays true\n"
        "to the checkout.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, Optional\n"
        "\n"
        f"JOBS = {jobs!r}\n"
        f"CATALOG = {catalog!r}\n"
        f"CAPABILITIES = {capabilities!r}\n"
        f"GATES = {gates!r}\n"
        "\n"
        "\n"
        "def _read_workspace_json(relative: str) -> Optional[Dict[str, Any]]:\n"
        "    path = Path(__file__).resolve().parents[1] / relative\n"
        "    if not path.is_file():\n"
        "        return None\n"
        "    try:\n"
        "        data = json.loads(path.read_text(encoding='utf-8'))\n"
        "    except (OSError, ValueError):\n"
        "        return None\n"
        "    return data if isinstance(data, dict) else None\n"
        "\n"
        "\n"
        "def _job(kernel: str) -> Dict[str, Any]:\n"
        "    for item in JOBS:\n"
        "        if item['kernel'] == kernel:\n"
        "            return dict(item)\n"
        "    return {'kernel': kernel}\n"
        "\n"
        "\n"
        "def inventory() -> Dict[str, Any]:\n"
        "    payload = _job('CLONER')\n"
        "    payload['lock'] = _read_workspace_json('blocks.lock.json') or {\n"
        "        'schema': 'blocks.lock.v1',\n"
        "        'blocks': {},\n"
        "    }\n"
        "    return payload\n"
        "\n"
        "\n"
        "def provenance() -> Dict[str, Any]:\n"
        "    payload = _job('STORE_MANAGER')\n"
        "    payload['build'] = _read_workspace_json('docs/build_provenance.json') or {}\n"
        "    lock = _read_workspace_json('blocks.lock.json') or {}\n"
        "    payload['clones'] = lock.get('blocks') or {}\n"
        "    payload['store_ops'] = []\n"
        "    return payload\n"
    )


def _render_routes(entries: List[Dict[str, Any]]) -> str:
    """FastAPI router: kernel job routes, then one POST/GET/GET-id per capability."""
    out = [
        '"""HTTP surface for the platform\'s kernels and capabilities.',
        "",
        "Kernel routes publish each build role's job. Capability routes run",
        "entirely in-process: the handler dispatches to a vendored block and",
        "the result is persisted locally. No outbound call.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any, Dict",
        "",
        "from fastapi import APIRouter, HTTPException, Request",
        "",
        "from app import jobs, store",
        "from app.domain_ops import perform as perform_domain",
        "from app.kernel_bridge import run_capability",
        "",
        "router = APIRouter()",
        "",
        "",
        '@router.get("/jobs")',
        "def list_jobs() -> Dict[str, Any]:",
        '    """Roster of every kernel job description."""',
        '    return {"jobs": jobs.JOBS}',
        "",
        "",
        '@router.get("/catalog")',
        "def catalog() -> Dict[str, Any]:",
        '    """COLLECTOR — Binding surveyor."""',
        "    return jobs.CATALOG",
        "",
        "",
        '@router.get("/inventory")',
        "def inventory() -> Dict[str, Any]:",
        '    """CLONER — Block stocker. Pinned vendor lock, read live."""',
        "    return jobs.inventory()",
        "",
        "",
        '@router.get("/capabilities")',
        "def capabilities() -> Dict[str, Any]:",
        '    """WRITER — Platform manufacturer. Capability HTTP surface."""',
        '    return {"items": jobs.CAPABILITIES}',
        "",
        "",
        '@router.get("/gates")',
        "def gates() -> Dict[str, Any]:",
        '    """TESTER — Acceptance inspector. Coverage only; does not run tests."""',
        "    return jobs.GATES",
        "",
        "",
        '@router.get("/provenance")',
        "def provenance() -> Dict[str, Any]:",
        '    """STORE_MANAGER — Store registrar. Clone register and provenance."""',
        "    return jobs.provenance()",
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
            "    try:",
            f"        result = _{name}_action.handle(payload)",
            "    except Exception as exc:  # Store/runtime refusal is not HTTP 500",
            '        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}',
            "    if not isinstance(result, dict):",
            '        return {"ok": False, "error": f"handle() returned {type(result).__name__}"}',
            "    return result",
            "",
            "",
            f'@router.post("/{name}")',
            f"async def {name}_create(payload: Dict[str, Any]) -> Dict[str, Any]:",
            f'    CAPABILITY_ID = "{e["capability_id"]}"',
            f"    handle = _{name}_handle",
            f'    save = lambda record: store.save("{entity}", record)',
            f'    list_all = lambda: store.list_all("{entity}")',
            e["body"],
            "",
            "",
            f'@router.get("/{name}")',
            f"def {name}_list(request: Request) -> Dict[str, Any]:",
            "    # F7: filter/sort/page from the entity's own declared columns.",
            "    # An unrecognised query field is refused rather than ignored --",
            "    # silently dropping ?staus=open returns the whole table and looks",
            "    # like a match.",
            '    CONTROLS = {"limit", "offset", "sort", "order"}',
            f'    allowed = set(store.COLUMNS["{entity}"]) | CONTROLS',
            "    given = dict(request.query_params)",
            "    unknown = sorted(k for k in given if k not in allowed)",
            "    if unknown:",
            '        return {"ok": False,',
            '                "error": "unknown query field(s): " + ", ".join(unknown)}',
            "    try:",
            f'        return store.query("{entity}",',
            "            filters={k: v for k, v in given.items() if k not in CONTROLS},",
            '            sort=given.get("sort"),',
            '            order=given.get("order", "asc"),',
            '            limit=int(given.get("limit", 50)),',
            '            offset=int(given.get("offset", 0)))',
            "    except (store.QueryError, ValueError) as exc:",
            '        return {"ok": False, "error": str(exc)}',
            "",
            "",
            f'@router.get("/{name}/{{item_id}}")',
            f"def {name}_get(item_id: int) -> Dict[str, Any]:",
            f'    record = store.get("{entity}", item_id)',
            "    if record is None:",
            '        raise HTTPException(status_code=404, detail="not found")',
            "    return record",
            "",
            "",
            f'@router.put("/{name}/{{item_id}}")',
            f"async def {name}_update(item_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:",
            f'    result = await perform_domain("update", "{e["capability_id"]}", '
            "{**(payload or {}), 'id': item_id})",
            "    if result.get('status') != 'success':",
            "        return {'ok': False,",
            "                'error': result.get('error_message') or result.get('status'),",
            "                'result': result}",
            "    return {'ok': True, 'capability': "
            f'"{e["capability_id"]}", \'result\': result}}',
            "",
            "",
            f'@router.delete("/{name}/{{item_id}}")',
            f"async def {name}_delete(item_id: int) -> Dict[str, Any]:",
            f'    result = await perform_domain("delete", "{e["capability_id"]}", '
            "{'id': item_id})",
            "    if result.get('status') != 'success':",
            "        return {'ok': False,",
            "                'error': result.get('error_message') or result.get('status'),",
            "                'result': result}",
            "    return {'ok': True, 'capability': "
            f'"{e["capability_id"]}", \'result\': result}}',
            "",
            "",
        ]
    out += [
        "@router.post(\"/work_queue\")",
        "async def work_queue_enqueue(payload: Dict[str, Any]) -> Dict[str, Any]:",
        "    result = await perform_domain(",
        '        "enqueue", str((payload or {}).get("capability_id") or ""), payload or {}',
        "    )",
        "    if result.get('status') != 'success':",
        "        return {'ok': False,",
        "                'error': result.get('error_message') or result.get('status'),",
        "                'result': result}",
        "    return {'ok': True, 'result': result}",
        "",
        "",
        '@router.post("/work_queue/{item_id}/process")',
        "async def work_queue_process(item_id: int) -> Dict[str, Any]:",
        '    result = await perform_domain("process", "", {"id": item_id})',
        "    if result.get('status') != 'success':",
        "        return {'ok': False,",
        "                'error': result.get('error_message') or result.get('status'),",
        "                'result': result}",
        "    return {'ok': True, 'result': result}",
        "",
        "",
        '@router.get("/work_queue")',
        "def work_queue_list() -> Dict[str, Any]:",
        "    from app import work_queue as _work_queue",
        '    return {"items": _work_queue.list_all()}',
        "",
        "",
    ]
    return "\n".join(out)


def _render_main(product_name: str) -> str:
    from app.factory.build.deploy import render_main

    return render_main(product_name)


#: Distributions the factory's own runtime lane declares. Named so the
#: vendored-dependency pass can dedupe against them instead of emitting a
#: second, conflicting line for the same package.
_RUNTIME_DISTRIBUTIONS = (
    "fastapi", "uvicorn", "pydantic", "alembic", "sqlalchemy", "starlette",
)


def _render_requirements(vendored_deps: Optional[Dict[str, Any]] = None) -> str:
    from app.factory.build.network_posture import POSTURE_ID

    return (
        "# Runtime dependencies. Persistence is stdlib sqlite3 on purpose --\n"
        f"# the platform runs with no database server and no network ({POSTURE_ID}).\n"
        "# pydantic is required by the vendored cerebrum_product_kernel contract.\n"
        "# Alembic applies versioned schema at deploy against STORAGE_PATH.\n"
        "# starlette is imported directly by app/observe.py. FastAPI pulls it in\n"
        "# transitively, but a runtime module that imports a package must declare\n"
        "# it: relying on someone else's dependency graph breaks the moment that\n"
        "# graph changes, and F10 is exactly the class of defect where an import\n"
        "# is satisfied by accident rather than by declaration.\n"
        "fastapi>=0.110\n"
        "uvicorn>=0.29\n"
        "pydantic>=2.0\n"
        "alembic>=1.13\n"
        "sqlalchemy>=2.0\n"
        "starlette>=0.37\n"
    ) + render_dependency_lines(
        vendored_deps or {}, already=_RUNTIME_DISTRIBUTIONS
    )


# -- deploy scaffold ------------------------------------------------------
#
# Templated, not coder-written, and deliberately so. Container and process
# config is mechanical: there is no domain judgement for an agent to
# contribute, and a hallucinated base image or start command is a deployment
# failure rather than a test failure. The dual path exists for artifacts where
# the agent knows something the template cannot.


def _render_dockerfile() -> str:
    from app.factory.build.network_posture import NETWORK_POSTURE

    text = (
        "# Standalone image. Blocks are vendored into the repository at build\n"
        f"# time. Network posture: {NETWORK_POSTURE} — no outbound at runtime.\n"
        f"# Base image pin: Docker Hub library/python:3.12-slim (2026-08-23).\n"
        f"FROM {PYTHON_312_SLIM_FROM}\n"
        "WORKDIR /app\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        "\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "COPY requirements-dev.txt .\n"
        "RUN pip install --no-cache-dir -r requirements-dev.txt\n"
        "\n"
        "COPY . .\n"
        "ENV PYTHONPATH=/app\n"
        "# Persistence is a sqlite file on the mounted disk (F23).\n"
        "ENV STORAGE_PATH=/app/data\n"
        "RUN mkdir -p /app/data\n"
        "\n"
        "# F19: a red suite must not produce a deployable image.\n"
        "RUN python3 scripts/release_gate.py\n"
        "\n"
        "EXPOSE 8000\n"
        "# S10: migrate against the persistent disk, then serve. Failure refuses boot.\n"
        "# scripts/entrypoint.sh: alembic upgrade head && uvicorn app.main:app\n"
        'ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]\n'
    )
    assert_generated_dockerfile(text)
    return text


def _render_dockerignore() -> str:
    return (
        "__pycache__/\n"
        "*.py[cod]\n"
        ".pytest_cache/\n"
        "data/\n"
        ".env\n"
        "build_ledger.jsonl\n"
    )


def _render_dev_requirements() -> str:
    """What scripts/release_gate.py needs in order to run.

    Kept out of requirements.txt so the delivered runtime stays lean, but it
    must EXIST: the artifact ships a clone-and-test gate and never declared
    the test runner that gate depends on. The identical omission in the
    factory's own production image made every live build fail its TESTER
    gate with "suite is red" and zero findings.
    """
    return (
        "# Needed by scripts/release_gate.py and tests/.\n"
        "#   pip install -r requirements-dev.txt\n"
        "pytest>=8\n"
        "httpx>=0.27\n"
    )


def _render_release_gate(product_name: str) -> str:
    """The clone-and-test contract, ported from the template path.

    A customer clones the delivered repository, runs one script, and watches
    the platform prove itself. The template generator has always shipped
    this; the runner's artifact must too, or the cutover would quietly drop
    a customer-facing promise. It additionally reports which artifacts the
    coding agent wrote, so provenance is auditable without the factory.
    """
    return f'''#!/usr/bin/env python3
"""Release gate for {product_name}.

Runs the platform's own *code-phase* suite (pytest -m "not pilot") and
prints a PASS/FAIL verdict. Store-backed execute-all lives on
@pytest.mark.pilot and is a later phase, not this gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    print("== {product_name} — release gate ==")
    try:
        import pytest  # noqa: F401
    except ImportError:
        print("pytest is not installed, so the suite cannot be run.")
        print("  pip install -r requirements-dev.txt")
        print("VERDICT: CANNOT RUN")
        return 2
    # Literal sys.executable. Extra braces make {{sys.executable}} a set,
    # which compiles then TypeError's in Popen (py_compile cannot catch it).
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-m", "not pilot"],
        cwd=ROOT,
    )
    ok = result.returncode == 0

    lock = ROOT / "blocks.lock.json"
    if lock.is_file():
        data = json.loads(lock.read_text(encoding="utf-8"))
        blocks = data.get("blocks", {{}})
        print(f"vendored blocks: {{len(blocks)}}")
        for bid, meta in sorted(blocks.items()):
            print(f"  {{bid}} @ {{str(meta.get('commit'))[:16]}} ({{meta.get('source')}})")
        runtime = data.get("runtime")
        if runtime:
            print(f"store runtime slice: {{len(runtime.get('files', []))}} file(s) "
                  f"@ {{str(runtime.get('commit'))[:16]}}")
    else:
        print("blocks.lock.json: MISSING — provenance of vendored blocks is unknown")
        ok = False

    manifest = ROOT / "docs" / "build_provenance.json"
    if manifest.is_file():
        prov = json.loads(manifest.read_text(encoding="utf-8"))
        sources = prov.get("artifact_sources", {{}})
        agent = sorted(k for k, v in sources.items() if str(v).startswith("coder LLM"))
        print(f"artifacts: {{len(sources)}} total, {{len(agent)}} written by the coding agent")
    else:
        print("docs/build_provenance.json: MISSING")
        ok = False

    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _render_procfile() -> str:
    return (
        "web: python -m alembic upgrade head && "
        "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}\n"
    )


def _render_platform_env_example() -> str:
    """P1_OFFLINE_STRICT: no store URL, no LLM keys, no Ollama.

    The template generator's .env.example documents CEREBRUM_API_URL because
    its handlers POST to the block store (S6 declared leftover). This
    platform's handlers import vendored blocks, so a store or cloud-LLM
    variable here would be a lie about how it runs.
    """
    from app.factory.build.network_posture import P1_ENV_EXAMPLE

    return P1_ENV_EXAMPLE


def _render_render_yaml(product_id: str) -> str:
    from app.factory.build.network_posture import NETWORK_POSTURE

    slug = re.sub(r"[^a-z0-9-]+", "-", str(product_id).lower()).strip("-") or "platform"
    return (
        "# Render blueprint. One web service, no database and no key-value\n"
        f"# store: persistence is a sqlite file on the mounted disk ({NETWORK_POSTURE}).\n"
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


def _kernel_http_readme_section() -> str:
    rows = []
    for job in jobs_manifest():
        routes = ", ".join(f"`{r}`" for r in job["http_routes"])
        rows.append(
            f"| `{job['kernel']}` | {job['title']} | {job['agent']} | {routes} |"
        )
    table = "\n".join(rows)
    return f"""

## Kernel jobs and HTTP

`GET /v1/jobs` lists every kernel's job description. Distinctive surfaces:

| Kernel | Job | Agent | Routes |
|---|---|---|---|
{table}

These routes inspect the manufactured platform. They do not re-run the factory
and they do not execute the test suite (`GET /v1/gates` describes coverage only).
"""


def _templated_readme(product_name: str, caps: Sequence[str], blocks: Sequence[str]) -> str:
    from app.factory.build.network_posture import NETWORK_POSTURE

    cap_lines = "\n".join(f"- `{c}`" for c in caps) or "- (none)"
    block_lines = "\n".join(f"- `{b}`" for b in blocks) or "- (none)"
    return f"""# {product_name}

Generated by the CerebrumDev factory role runner.

## What this is

A standalone platform. Every capability runs in-process: handlers invoke
blocks that were vendored into `vendor/blocks/` at build time, through
`app/dispatch.py`. There is **no call back to a block store at runtime** — the
platform runs with the factory switched off (`{NETWORK_POSTURE}`).

## Capabilities

{cap_lines}

## Vendored blocks

{block_lines}

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload      # GET /health -> 200; GET /v1/jobs -> kernel JDs
.venv/bin/python -m pytest tests -m "not pilot"   # factory code-phase gate
.venv/bin/python -m pytest tests                   # includes Store-backed @pytest.mark.pilot
# or: .venv/bin/python scripts/release_gate.py
```

Data lands in `$STORAGE_PATH/platform.db` (default `./data`), stdlib sqlite3.
Schema is Alembic (`alembic upgrade head` at deploy). `app/store.py` does not
`CREATE TABLE`.

## Layout

| Path | Purpose |
|---|---|
| `app/jobs.py` | kernel job descriptions (`GET /v1/jobs` and friends) |
| `app/models.py` | domain dataclasses |
| `app/store.py` | sqlite persistence (WAL; no DDL) |
| `app/migrations.py` | Alembic upgrade / downgrade |
| `app/backup.py` | backup / restore / retention |
| `alembic/` | versioned up and down revisions |
| `app/routes.py` | HTTP surface |
| `app/actions/` | capability handlers |
| `app/dispatch.py` | local block dispatch |
| `vendor/blocks/` | vendored block source, pinned by `blocks.lock.json` |
| `kits/` | kit packs for the capabilities (Factory shelf / Blocks kits) |
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
        # Blocks self-document their per-action requirements in the error
        # literals they answer with ("user_id and name required", "metric and
        # value required", ...). Three live builds discovered these one 429
        # at a time; harvesting them into the contract lets the coder satisfy
        # them before the first attempt instead of after the third.
        errors = re.findall(
            r"[\"']error[\"']\s*:\s*f?[\"']([^\"'{}]{4,120})[\"']", source
        )
        if errors:
            contract["runtime_error_contracts"] = sorted(set(errors))[:25]
        # WORKAROUND -- removal tracked in CerebrumDev.ai#256.
        #
        # The dict keys the block's code reads from its input are its de
        # facto vocabulary. A pipeline step written as {"block_id": ...}
        # failed with "No block specified" because the workflow block reads
        # step.get("block") -- knowledge that sat in the vendored source and
        # nowhere else, which is why this harvest exists at all.
        #
        # It reads the wrong artifact, and that is not a bug to fix here.
        # A .get("x") anywhere in the module harvests identically whether x
        # is a payload key, a result key, a config key or an env key; a key
        # read through a variable (payload.get(field)) is invisible to it;
        # and the cap of 40 is arbitrary. Only the kit author knows which
        # keys are the contract.
        #
        # The design is Cerebrum-Blocks#90 (Lane 2 / L2.2): block.json
        # declaring requires_inputs. Until it lands AND the vendored blocks
        # fill it in -- it ships optional and empty on purpose -- a declared
        # value wins over this one. The two are never merged silently.
        keys_read = re.findall(r"\.get\(\s*[\"'](\w{2,30})[\"']", source)
        if keys_read:
            contract["input_keys_read_by_block"] = sorted(set(keys_read))[:40]
    return contract


def _budget_too_low(ctx: RoleContext, what: str) -> bool:
    """True when too little build budget remains for another coder call.

    Records the skip so the artifact's provenance says the agent was not
    used and why -- an artifact quietly templated because time ran out is
    exactly the invisible degradation the factory refuses.

    A stage-inspect hard-stop must not stub remaining capabilities as a
    thin SUCCESS: raise so the run fails closed with the inspect snapshot.
    """
    halt = (ctx.state or {}).get("stage_halt")
    if isinstance(halt, dict) and halt.get("decision") == "hard_stop":
        raise RoleError(
            halt.get("reason")
            or f"stage inspect hard-stop before {what}"
        )
    left = ctx.coder_time_left()
    if left is None:
        return False
    from app.factory.coder import _call_timeout_s

    # One successful post has to finish inside the remaining phase wall.
    # Requiring two full legs here skipped every handler once the
    # per-attempt timeout grew past the old 25-minute code-phase cap.
    needed = _call_timeout_s() + 30
    if left > needed:
        return False
    ctx.state.setdefault("coder_failures", {})[what] = (
        f"skipped: {int(max(left, 0))}s of build budget left, a {what} call "
        f"needs up to {int(needed)}s"
    )
    ctx.note(f"coder skipped for {what} — build budget nearly spent", stage="budget")
    return True


def _note_model_call(ctx: RoleContext, what: str, **payload: Any) -> None:
    """Ledger NOTE that a coder LLM call is in flight — not progress.

    ``done`` is left to the caller so a hung call cannot look like a
    finished handler. ``model_call`` + ``deadline_s`` let build_status
    fail the Floor once the watchdog wall is exceeded instead of climbing
    "quiet for N min — model call may still be running".
    """
    from app.factory.coder import _attempt_wall_s

    ctx.note(
        f"calling coder LLM for {what}",
        model_call=True,
        deadline_s=_attempt_wall_s(),
        **payload,
    )


def _coder_body(
    ctx: RoleContext,
    cap: Any,
    usable: Sequence[str],
    spec: Optional[Dict[str, Any]] = None,
    previous_attempt: Optional[str] = None,
) -> Optional[tuple]:
    """Ask the coding agent for this handler's body, or None if unavailable.

    Returns ``(body, source)``. None means the agent could not be used --
    disabled, unconfigured, or it failed its validation gate. The caller then
    ships the deterministic body and records *which* path produced it, so the
    artifact never implies authorship it did not have.

    ``spec`` is the capability's data model. Without it the coder invents its
    own required payload fields, and the suite -- which builds payloads from
    the spec -- rejects the handler for demanding fields no caller sends.
    """
    from app.factory.coder import (
        CoderError,
        CoderTimeout,
        coder_enabled,
        generate_platform_handler,
    )

    if not coder_enabled():
        return None
    if _budget_too_low(ctx, "handler"):
        return None
    _note_model_call(
        ctx,
        cap.capability_id,
        stage="coder",
        capability=cap.capability_id,
    )
    try:
        result = generate_platform_handler(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            block_ids=list(usable),
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
            work_list=list(ctx.work_list),
            block_contracts={b: _block_contract(ctx, b) for b in usable},
            model_fields=(spec or {}).get("fields"),
            resource_obligations=describe_resource_obligations(usable),
            previous_attempt=previous_attempt,
            vendored_roster=sorted(ctx.state.get("vendored_blocks", ())),
        )
    except CoderTimeout as exc:
        ctx.state.setdefault("coder_failures", {})[cap.capability_id] = str(exc)
        raise RoleError(
            f"coder LLM timed out writing handler {cap.capability_id}: {exc}"
        ) from exc
    except CoderError as exc:
        # Degraded output is acceptable; invisible degradation is not.
        ctx.state.setdefault("coder_failures", {})[cap.capability_id] = str(exc)
        return None
    return result["body"], f"coder LLM ({result['model']})"


def _record_failure(ctx: RoleContext, key: str, exc: Exception) -> None:
    ctx.state.setdefault("coder_failures", {})[key] = str(exc)


def _coder_model_spec(ctx: RoleContext, cap: Any) -> Optional[Dict[str, Any]]:
    """Let the agent design the schema. None means fall back to the template."""
    from app.factory.coder import (
        CoderError,
        CoderTimeout,
        coder_enabled,
        generate_model_spec,
    )

    if not coder_enabled():
        return None
    if _budget_too_low(ctx, "model spec"):
        return None
    _note_model_call(
        ctx,
        f"model spec {cap.capability_id}",
        stage="coder",
        capability=cap.capability_id,
    )
    try:
        return generate_model_spec(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
        )
    except CoderTimeout as exc:
        _record_failure(ctx, f"model:{cap.capability_id}", exc)
        raise RoleError(
            f"coder LLM timed out writing model spec {cap.capability_id}: {exc}"
        ) from exc
    except CoderError as exc:
        _record_failure(ctx, f"model:{cap.capability_id}", exc)
        return None


def _coder_route_body(
    ctx: RoleContext,
    cap: Any,
    spec: Dict[str, Any],
    previous_attempt: Optional[str] = None,
) -> Optional[tuple]:
    """Capability HTTP routes go through execute_action (U12).

    An LLM-authored body would displace the kernel. Handlers and models may
    still be coder-written; the route is not. This is not a persist-wrapper.
    """
    return None


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
        from app.factory.coder import _llm_code_call

        text, model_used = _llm_code_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Write a concise README.md for a generated business platform. "
                        "Markdown only, no code fences around the whole document. "
                        "Cover: what it does, its capabilities, how to run it "
                        "(python3 -m venv .venv; "
                        ".venv/bin/pip install -r requirements.txt -r requirements-dev.txt; "
                        ".venv/bin/uvicorn app.main:app; GET /health and GET /v1/jobs; "
                        ".venv/bin/python -m pytest tests -m \"not pilot\"), "
                        "and that it runs fully offline with blocks vendored into "
                        "vendor/blocks/ and no call back to any store. Do not invent "
                        "an HTTP table — a kernel-jobs section is appended for you."
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
    return text, f"coder LLM ({model_used})"


def _failing_capability_ids(work_list: Sequence[str], cap_ids: Sequence[str]) -> set:
    """Which capabilities the TESTER's findings actually implicate.

    On the seventh live build the loop fixed defect_register and REGRESSED
    site_inspection_log, because every rework round regenerated every
    handler -- a nondeterministic coder given the whole platform each round
    plays whack-a-mole. A rework pass must be a ratchet: touch only what
    failed, keep what passed.

    Empty findings (the first pass) mean everything. Findings that name no
    capability mean everything too -- an infrastructure failure cannot be
    localised, and guessing "nothing" would end the rework with the suite
    still red.
    """
    if not work_list:
        return set(cap_ids)
    text = "\n".join(str(item) for item in work_list)
    failing = {
        cap_id
        for cap_id in cap_ids
        # Word-bounded: a short capability id must not match inside an
        # unrelated word of the traceback.
        if re.search(rf"\b{re.escape(cap_id)}\b", text)
        or re.search(rf"\b{re.escape(cap_id.replace('-', '_'))}\b", text)
    }
    return failing or set(cap_ids)


def _vendor_product_kernel(ctx: RoleContext) -> None:
    """Copy the host kernel into the generated product (U12).

    Same source ProductGenerator vendors at generator.py. role_runner previously
    shipped none of it. Imports already use ``app.cerebrum_product_kernel``.
    """
    kernel_src = Path(__file__).resolve().parents[2] / "cerebrum_product_kernel"
    if not kernel_src.is_dir():
        raise RoleError(
            "cerebrum_product_kernel missing from factory host — cannot ship U12"
        )
    for item in sorted(kernel_src.rglob("*")):
        if not item.is_file():
            continue
        if "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"}:
            continue
        rel = Path("app") / "cerebrum_product_kernel" / item.relative_to(kernel_src)
        ctx.workspace.copy_file(item, rel)


def _render_kernel_bridge() -> str:
    """Adapt sync capability handle() to execute_action. Does not own persist."""
    return '''"""Bridge capability handle() through the vendored product kernel.

The kernel owns trust-scope, input/output validation, and ActionResult.
Persistence stays in the HTTP route after ActionStatus.SUCCESS.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.runtime import execute_action


def _wrap_handle(handle):
    async def _handler(context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
        # Live invoice-management (2026-08-30): a coder handler returned
        # ok:True after execute() failed. Without watching the seam, this
        # wrapper treated that as ActionStatus.SUCCESS and the route
        # persisted. A capability route must not report success over a
        # failed block, whichever path wrote the handler.
        import sys
        import app.dispatch as _dispatch

        _real = _dispatch.execute
        _failed = []

        def _watched(block_id, *a, **kw):
            res = _real(block_id, *a, **kw)
            if isinstance(res, dict) and (
                res.get("status") == "error" or "error" in res
            ):
                _failed.append(str(block_id))
            return res

        _dispatch.execute = _watched
        _patched = []
        for _name, _mod in list(sys.modules.items()):
            if _name.startswith("app.actions") and hasattr(_mod, "execute"):
                _patched.append((_mod, _mod.execute))
                _mod.execute = _watched
        try:
            out = handle(arguments)
        finally:
            _dispatch.execute = _real
            for _mod, _prev in _patched:
                _mod.execute = _prev
        if not isinstance(out, dict):
            return ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="invalid_handler_result",
                error_message="handle() returned a non-mapping",
            )
        if out.get("ok") is False:
            return ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="refused",
                error_message=str(out.get("error") or "refused"),
                output=out,
            )
        if _failed:
            return ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="block_failed",
                error_message="block(s) failed: " + ", ".join(_failed),
                output=out,
            )
        return ActionOutcome.success(out)

    return _handler


def spec_for(capability_id: str) -> ActionSpec:
    name = capability_id.replace("-", "_")
    mod = importlib.import_module(f"app.actions.{name}")
    return ActionSpec(
        action_id=f"product.{name}",
        domain="product",
        name=name,
        description=str(getattr(mod, "CAPABILITY_ID", name)),
        # The capability's own domain columns. Without them the kernel strips
        # any column named like a trust-scope key -- project_id, user_id,
        # tenant_id -- and the handler refuses its own well-formed payload.
        input_schema={
            "properties": {
                str(f): {} for f in (getattr(mod, "CAPABILITY_FIELDS", ()) or ())
            }
        },
        output_schema={},
        required_context=[],
        permissions=[],
        read_only=False,
        handler=_wrap_handle(mod.handle),
    )


def product_context() -> ActionContext:
    return ActionContext(
        user_id="anonymous",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[],
        allowed_domains=["product"],
    )


async def run_capability(capability_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    result = await execute_action(spec_for(capability_id), product_context(), payload or {})
    return result.to_dict()
'''


def _writer_block_roster(state: Dict[str, Any]) -> tuple:
    """Block ids this WRITER pass may emit preconditions for.

    Frozen at entry so later handler/domain/supply-chain work cannot expand
    the roster to the whole vendor mirror (CI saw analytics..workflow).
    A mapping is treated as id→path (kit_pack shape), not as 'use every key
    of RESOURCE_OBLIGATIONS / the factory shelf'.
    """
    raw = (state or {}).get("vendored_blocks") or ()
    if isinstance(raw, dict):
        raw = raw.keys()
    return tuple(sorted({str(b) for b in raw if b}))


def run_writer(ctx: RoleContext) -> RoleResult:
    """Platform manufacturer: dispatch runtime plus one handler per capability.

    The coding agent writes each body when one is configured; otherwise the
    body is composed from the block contract deterministically. Which path ran
    is stamped into every module header and reported in the result -- CI has
    no key and must still exercise this route, so the fallback is a first-class
    path rather than an error. Also publishes every kernel's job over HTTP
    (``app/jobs.py``, kernel routes in ``app/routes.py``).

    On a rework pass ``ctx.work_list`` carries the TESTER's findings. Only the
    capabilities those findings implicate are regenerated; everything else is
    reused from the previous round (specs from state, handler files from the
    committed destination, route bodies from state), so a green capability
    cannot regress while a red one is being fixed.

    A pilot cycle without a coder key must not replace agent-written
    handlers with the deterministic template. Adapter patches already ran.
    """
    writer_roster = _writer_block_roster(ctx.state)
    if (
        str(ctx.state.get("build_cycle") or "") == "pilot"
        and ctx.work_list
    ):
        from app.factory.coder import coder_enabled

        if not coder_enabled():
            # Do not report ok=True: that would re-enter TESTER with identical
            # handlers and burn the rework budget on a no-op loop.
            vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
            return RoleResult(
                ok=False,
                detail=(
                    "pilot rework requires a coder key; cannot regenerate failing "
                    f"handlers ({len(ctx.plan.capabilities)} capability(ies) unchanged)"
                ),
                vendored_blocks=tuple(vendored),
            )
    ctx.workspace.write_text(Path("app") / "__init__.py", '"""Generated platform."""\n')
    _vendor_product_kernel(ctx)
    ctx.workspace.write_text(Path("app") / "kernel_bridge.py", _render_kernel_bridge())
    vendored_ids = [b for b in ctx.state.get("vendored_blocks", ()) if b]
    contracts = {b: _block_contract(ctx, b) for b in vendored_ids}
    ctx.workspace.write_text(Path("app") / "dispatch.py", _render_dispatch(contracts))
    # Shared block-input construction for every handler's execute wrapper.
    # Lives beside dispatch (not inside it) so LotDesk F18 stays clean.
    ctx.workspace.write_text(Path("app") / "block_inputs.py", render_block_inputs_module())

    vendored = set(ctx.state.get("vendored_blocks", ()))
    cap_ids = [cap.capability_id for cap in ctx.plan.capabilities]
    failing = _failing_capability_ids(ctx.work_list, cap_ids)
    previous_specs: Dict[str, Any] = dict(ctx.state.get("model_specs") or {})
    previous_sources: Dict[str, str] = dict(ctx.state.get("artifact_sources") or {})
    previous_routes: Dict[str, str] = dict(ctx.state.get("route_bodies") or {})

    written: List[str] = []
    sources: Dict[str, str] = {}
    fallback_source = "deterministic contract template"

    # --- domain models FIRST: handlers and routes are written against them --
    specs: Dict[str, Dict[str, Any]] = {}
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        if cid not in failing and cid in previous_specs:
            # A ratcheted spec gets the same obligation check: the round that
            # produced it may predate the block assignment it is reused with.
            _kept = [b for b in cap.block_ids if b in vendored]
            specs[cid] = augment_model_spec(previous_specs[cid], _kept)
            specs[cid], _envelope = ensure_record_envelope(specs[cid])
            assert_feedable(cid, _kept, specs[cid])
            sources[f"model:{cid}"] = previous_sources.get(
                f"model:{cid}", "unchanged from previous round"
            )
            continue
        spec = _coder_model_spec(ctx, cap) or _fallback_spec(cap)
        # IF YOU ASSIGN IT, YOU FEED IT. A block whose precondition only the
        # caller can meet obligates this spec to carry a field for it; the
        # agent still owns the design, this only closes what it left open.
        # Audited immediately after, so an unfeedable assignment fails HERE
        # with the missing field named -- not at the F11 gate, after four
        # handlers were written, with no zip.
        _assigned = [b for b in cap.block_ids if b in vendored]
        spec = augment_model_spec(spec, _assigned)
        spec, _envelope = ensure_record_envelope(spec)
        assert_feedable(cid, _assigned, spec)
        specs[cid] = spec
        sources[f"model:{cid}"] = (
            f"coder LLM ({spec['model']})" if spec.get("model") else fallback_source
        )
    ctx.note(
        f"designed {len(specs)} data model(s)",
        stage="models",
        done=len(specs),
        total=len(cap_ids),
    )
    ctx.workspace.write_text(Path("app") / "models.py", _render_models(specs))

    # Persistence + versioned Alembic from the same specs. connect() does
    # not CREATE TABLE — deploy applies revisions against STORAGE_PATH.
    from app.factory.build.data_lifecycle import emit_writer_artifacts
    from app.factory.build.deploy import emit_writer_artifacts as emit_deploy_artifacts
    from app.factory.build.domain_acceptance import (
        emit_writer_artifacts as emit_domain_artifacts,
    )

    emit_writer_artifacts(ctx.workspace, specs)
    emit_deploy_artifacts(ctx.workspace)
    emit_domain_artifacts(ctx.workspace, specs)
    sources["persistence"] = (
        "derived from coder-designed models"
        if any(s.get("model") for s in specs.values())
        else fallback_source
    )
    sources["migrations"] = "alembic revisions emitted from unused-kit pattern"
    sources["deploy_observe"] = "S11 fail-closed health, rollback drill, JSON request logs"
    sources["domain_acceptance"] = (
        "S12 ten outcomes through execute_action; LotDesk-class fixtures fail"
    )

    # --- capability handlers ------------------------------------------------
    actions_init = ['"""Capability handlers."""', ""]
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        name = cid.replace("-", "_")
        handler_rel = Path("app") / "actions" / f"{name}.py"
        actions_init.append(f"from app.actions import {name}  # noqa: F401")
        written.append(name)

        if cid not in failing and ctx.workspace.exists(handler_rel):
            # Ratchet: the previous round's handler passed; keep it.
            sources[cid] = previous_sources.get(cid, "unchanged from previous round")
            continue

        usable = [b for b in cap.block_ids if b in vendored]
        # On rework, hand the coder its own last attempt: eight live rounds
        # proved that regenerating from the same prompt converges to the
        # same wrong code, verbatim.
        previous_attempt = (
            ctx.workspace.read_text(handler_rel)
            if ctx.work_list and ctx.workspace.exists(handler_rel)
            else None
        )
        authored = _coder_body(ctx, cap, usable, specs[cid], previous_attempt)
        body, source = authored or (_templated_body(usable), fallback_source)

        default_actions = {
            b: contract["default_action"]
            for b in usable
            if (contract := _block_contract(ctx, b)).get("default_action")
        }
        ctx.workspace.write_text(
            handler_rel,
            _handler_module(
                cid, usable, body, source, default_actions,
                field_names=[
                    str(f.get("name"))
                    for f in ((specs.get(cid) or {}).get("fields") or [])
                    if isinstance(f, dict) and f.get("name")
                ],
                entity=str((specs.get(cid) or {}).get("entity") or name),
            ),
        )
        sources[cid] = source
        ctx.note(
            f"wrote handler {cid} ({source})",
            stage="handlers",
            capability=cid,
            source=source,
            done=len([k for k in sources if k in set(cap_ids)]),
            total=len(cap_ids),
        )

    ctx.workspace.write_text(
        Path("app") / "actions" / "__init__.py", "\n".join(actions_init) + "\n"
    )

    # Handlers may validate domain fields the model_specs omitted (live:
    # property_reference_code) or enforce a vocabulary/type the spec left as
    # bare str (live VetConnect role/channel/is_active/login_count). Align
    # specs so _sample_payload and the route guard agree with the handler
    # before routes/tests are written.
    aligned_specs = False
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        name = cid.replace("-", "_")
        handler_rel = Path("app") / "actions" / f"{name}.py"
        if not ctx.workspace.exists(handler_rel):
            continue
        specs[cid], changed = align_spec_to_handler_source(
            specs[cid], ctx.workspace.read_text(handler_rel)
        )
        specs[cid], env_added = ensure_record_envelope(specs[cid])
        changed = list(changed) + list(env_added)
        if not changed:
            continue
        aligned_specs = True
        field_names = [
            str(f.get("name"))
            for f in (specs[cid].get("fields") or [])
            if isinstance(f, dict) and f.get("name")
        ]
        text = ctx.workspace.read_text(handler_rel)
        text = re.sub(
            r"^CAPABILITY_FIELDS = .*$",
            f"CAPABILITY_FIELDS = {field_names!r}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        ctx.workspace.write_text(handler_rel, text)
        ctx.note(
            f"aligned model_specs for {cid} with handler-required fields: "
            + ", ".join(changed),
            stage="models",
            capability=cid,
            added=changed,
        )
    if aligned_specs:
        ctx.workspace.write_text(Path("app") / "models.py", _render_models(specs))
        emit_writer_artifacts(ctx.workspace, specs)
        emit_domain_artifacts(ctx.workspace, specs)

    # --- API surface -------------------------------------------------------
    entries: List[Dict[str, Any]] = []
    route_bodies: Dict[str, str] = {}
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        spec = specs[cid]
        if cid not in failing and cid in previous_routes:
            body = previous_routes[cid]
            route_source = previous_sources.get(
                f"route:{cid}", "unchanged from previous round"
            )
        else:
            authored = _coder_route_body(
                ctx,
                cap,
                spec,
                previous_attempt=previous_routes.get(cid) if ctx.work_list else None,
            )
            body, route_source = authored or (
                _templated_route_body(spec),
                "kernel execute_action template",
            )
            if authored:
                # Coder bodies skip field constraints; the tasting-room
                # TESTER then failed agent "reject" cases the route accepted.
                body = _constraint_guard(spec) + "\n" + body
        body = _ensure_route_persists_payload(body)
        route_bodies[cid] = body
        name = cid.replace("-", "_")
        if name in KERNEL_ROUTE_NAMES:
            raise RoleError(
                f"capability {cid} collides with kernel HTTP route /v1/{name}"
            )
        entries.append(
            {
                "capability_id": cid,
                "name": name,
                "entity": spec["entity"],
                "body": body,
                "source": route_source,
            }
        )
        sources[f"route:{cid}"] = route_source
        ctx.note(
            f"wrote route {cid} ({route_source})",
            stage="routes",
            capability=cid,
            source=route_source,
            done=len(entries),
            total=len(cap_ids),
        )
    gaps = {str(g) for g in (ctx.state.get("gaps") or ())}
    catalog = {
        "kernel": "COLLECTOR",
        "title": role_contract(BuildRole.COLLECTOR).title,
        "mandate": role_contract(BuildRole.COLLECTOR).mandate,
        "agent": role_contract(BuildRole.COLLECTOR).agent.value,
        "resolved_blocks": list(
            ctx.state.get("resolved_blocks") or ctx.state.get("vendored_blocks") or []
        ),
        "gaps": list(ctx.state.get("gaps") or []),
        "bindings": [
            {
                "capability_id": cap.capability_id,
                "block_ids": list(cap.block_ids or []),
                "gap": cap.capability_id in gaps or not cap.block_ids,
            }
            for cap in ctx.plan.capabilities
        ],
        "agent_reviews": list(ctx.state.get("agent_binding_reviews") or []),
        "agent_model": ctx.state.get("agent_binding_model") or "",
    }
    capabilities = [
        {
            "id": e["capability_id"],
            "entity": e["entity"],
            "source": e["source"],
            "http": {
                "create": f"POST /v1/{e['name']}",
                "list": f"GET /v1/{e['name']}",
                "get": f"GET /v1/{e['name']}/{{id}}",
                "update": f"PUT /v1/{e['name']}/{{id}}",
                "delete": f"DELETE /v1/{e['name']}/{{id}}",
            },
        }
        for e in entries
    ]
    tester = role_contract(BuildRole.TESTER)
    gates = {
        "kernel": tester.role.value,
        "title": tester.title,
        "mandate": tester.mandate,
        "agent": tester.agent.value,
        "runs_over_http": False,
        "suite": [
            {
                "file": "tests/test_smoke.py",
                "covers": "import, offline dispatch load, handle() returns a mapping",
                "gated": True,
            },
            {
                "file": "tests/test_smoke.py",
                "covers": "Store-backed handle() ok and nested error scan",
                "marker": "pilot",
                "gated": False,
            },
            {
                "file": "tests/test_models.py",
                "covers": "sqlite round-trip via store.save / store.get",
                "gated": True,
            },
            {
                "file": "tests/test_data_lifecycle.py",
                "covers": "Alembic up/down on populated v1, restore drill, parallel writes",
                "gated": True,
            },
            {
                "file": "tests/test_deploy.py",
                "covers": "Fail-closed /health, correlation logs, revision rollback identity",
                "gated": True,
            },
            {
                "file": "tests/test_domain_acceptance.py",
                "covers": "Ten business outcomes through execute_action",
                "marker": "pilot",
                "gated": False,
            },
            {
                "file": "tests/test_routes.py",
                "covers": "HTTP 200 JSON for /health, kernel jobs, and each capability POST",
                "gated": True,
            },
            {
                "file": "tests/test_routes.py",
                "covers": "Store-backed POST accepted (ok is not False) and persisted",
                "marker": "pilot",
                "gated": False,
            },
            {
                "file": "tests/agent_domain_cases.py",
                "covers": "optional coding-agent domain mutations of spec payloads",
                "optional": True,
                "gated": False,
            },
        ],
    }
    ctx.workspace.write_text(
        Path("app") / "jobs.py",
        _render_jobs_module(
            catalog=catalog, capabilities=capabilities, gates=gates
        ),
    )
    sources["jobs"] = fallback_source
    ctx.workspace.write_text(Path("app") / "routes.py", _render_routes(entries))

    # --- run scaffold ------------------------------------------------------
    product_name = getattr(ctx.blueprint, "product_name", "Generated Platform")
    ctx.workspace.write_text(Path("app") / "main.py", _render_main(product_name))
    # Platform preconditions as CODE, not as prose in a coder prompt (R1c).
    # The prompt told every handler to create the team first; on
    # residential-lettings three of four did not, and the one that used the
    # correct calling convention still answered "Team access denied".
    ctx.workspace.write_text(
        Path("app") / "preconditions.py",
        render_preconditions_module(
            list(writer_roster),
            product_name,
        ),
    )
    # A vendored block's imports are a precondition exactly like a schema
    # field: assigned means declared. Derived from the source the CLONER
    # actually wrote, so it cannot drift from what ships.
    ctx.workspace.write_text(
        "requirements.txt",
        _render_requirements(ctx.state.get("vendored_dependencies")),
    )
    ctx.workspace.write_text(
        "requirements-dev.txt", _render_dev_requirements()
    )
    if ctx.work_list and ctx.workspace.exists("README.md"):
        # The README does not fail tests; regenerating it on rework spends
        # coder budget for churn.
        sources["readme"] = previous_sources.get("readme", "unchanged from previous round")
    else:
        readme = _coder_readme(ctx, product_name, written, sorted(vendored))
        body = (
            readme[0]
            if readme
            else _templated_readme(product_name, written, sorted(vendored))
        )
        from app.factory.build.network_posture import readme_section

        ctx.workspace.write_text(
            "README.md", body + _kernel_http_readme_section() + readme_section()
        )
        sources["readme"] = readme[1] if readme else fallback_source
    sources["entrypoint"] = fallback_source
    sources["requirements"] = fallback_source

    # Deploy scaffold: without these the artifact can only be run locally.
    product_id = getattr(ctx.blueprint, "product_id", "platform")
    ctx.workspace.write_text("Dockerfile", _render_dockerfile())
    ctx.workspace.write_text(".dockerignore", _render_dockerignore())
    ctx.workspace.write_text("Procfile", _render_procfile())
    ctx.workspace.write_text(
        Path("scripts") / "release_gate.py", _render_release_gate(product_name)
    )
    sources["release_gate"] = fallback_source
    ctx.workspace.write_text(".env.example", _render_platform_env_example())
    ctx.workspace.write_text("render.yaml", _render_render_yaml(product_id))
    from app.factory.build.network_posture import POSTURE_ID, declaration_json

    ctx.workspace.write_text(Path("docs") / "network_posture.json", declaration_json())
    try:
        emit_supply_chain_artifacts(
            ctx.workspace,
            product_id=str(product_id),
            product_name=str(product_name),
            vendored_blocks=sorted(vendored),
        )
    except SupplyChainError as exc:
        raise RoleError(str(exc)) from exc
    from app.factory.build.domain_pack import DomainPackError, emit_domain_pack

    try:
        emit_domain_pack(ctx.workspace, blueprint=ctx.blueprint)
    except DomainPackError as exc:
        raise RoleError(str(exc)) from exc
    sources["deploy_scaffold"] = fallback_source
    sources["network_posture"] = POSTURE_ID
    sources["sbom"] = fallback_source
    sources["permissions"] = fallback_source
    sources["domain_pack"] = fallback_source

    from app.factory.build.converge import converge_writer_emitters

    converged = converge_writer_emitters(ctx)
    if converged.get("ok"):
        sources["emitter_parity"] = "ProductGenerator class emitters (converge)"

    from app.factory.build.converge import converge_writer_emitters

    converged = converge_writer_emitters(ctx)
    if converged.get("ok"):
        sources["emitter_parity"] = "ProductGenerator class emitters (converge)"

    by_coder = sum(1 for s in sources.values() if s.startswith("coder LLM"))
    ctx.workspace.write_text(
        Path("docs") / "build_provenance.json",
        json.dumps(
            {
                "schema_version": "build_provenance.v1",
                "product_id": getattr(ctx.blueprint, "product_id", "unknown"),
                "product_name": product_name,
                "engine": "role_runner",
                "network_posture": POSTURE_ID,
                "artifact_sources": sources,
                "coder_failures": dict(ctx.state.get("coder_failures", {})),
                "kernel_agents": {
                    "COLLECTOR": {
                        "reviews": list(ctx.state.get("agent_binding_reviews") or []),
                        "model": ctx.state.get("agent_binding_model") or "",
                    },
                    "WRITER": {"artifacts": by_coder},
                    "TESTER": "consults the coding agent after this file is written",
                    "CLONER": "deterministic — no agent",
                    "STORE_MANAGER": "deterministic — no agent",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    from app.factory.build.network_posture import PostureError, assert_workspace_posture

    try:
        assert_workspace_posture(
            ctx.workspace.workspace,
            fallback=(
                ctx.workspace.destination if ctx.workspace.staged else None
            ),
        )
    except PostureError as exc:
        raise RoleError(str(exc)) from exc
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
            # Kept for the next rework round's ratchet: bodies of routes that
            # passed are reused verbatim instead of regenerated.
            "route_bodies": route_bodies,
        },
    )


# -- TESTER --------------------------------------------------------------


def _looks_like_email_field(field: Dict[str, Any]) -> bool:
    """Field name or format implies an address, not the word 'sample'.

    The live winery-hospitality export failed its own pilot suite because
    TESTER sent guest_email='sample' and WRITER (correctly) required '@'.
    Vocabulary/min/max cannot express that; the name is the constraint.
    """
    if str(field.get("type") or "str") != "str":
        return False
    fmt = str(field.get("format") or "").lower().replace("-", "")
    if fmt == "email":
        return True
    name = str(field.get("name") or "").lower()
    return name == "email" or name.endswith("_email") or name.startswith("email_")


_TYPE_ALIASES = {
    "str": "str",
    "text": "str",
    "string": "str",
    "int": "int",
    "integer": "int",
    "float": "float",
    "bool": "bool",
    "boolean": "bool",
    "datetime": "datetime",
    "timestamp": "datetime",
    "date": "date",
    "time": "time",
}

_TEMPORAL_SAMPLES = {
    "datetime": "2026-09-03T10:00:00",
    "date": "2026-09-03",
    "time": "10:00:00",
}


def _normalize_field_type(raw: Any) -> str:
    """Map LLM/SQL/annotation spellings onto the emitter's type set."""
    kind = str(raw or "str").strip().lower()
    kind = kind.replace("datetime.", "").replace("optional[", "").replace("]", "")
    return _TYPE_ALIASES.get(kind, "str")


def _python_annotation(field: Dict[str, Any]) -> str:
    """Compilable dataclass annotation. datetime/date/time persist as ISO str."""
    ftype = _normalize_field_type(field.get("type") or "str")
    return {
        "str": "str",
        "int": "int",
        "float": "float",
        "bool": "bool",
        "datetime": "str",
        "date": "str",
        "time": "str",
    }.get(ftype, "str")


def _temporal_sample(field: Dict[str, Any]) -> str | None:
    """ISO sample for appointment-like fields, or None when not temporal.

    Live veterinary-care: scheduled_time / duration_minutes / service_type.
    The word "sample" is type-valid as str and rejected as a time.
    """
    fmt = str(field.get("format") or "").lower().replace("-", "")
    ftype = _normalize_field_type(field.get("type") or "str")
    name = str(field.get("name") or "").lower()
    if ftype == "datetime" or fmt in ("datetime", "timestamp", "iso8601"):
        return _TEMPORAL_SAMPLES["datetime"]
    if ftype == "date" or fmt == "date":
        return _TEMPORAL_SAMPLES["date"]
    if ftype == "time" or fmt == "time":
        return _TEMPORAL_SAMPLES["time"]
    if name.endswith("_at") or name.endswith("_datetime"):
        return _TEMPORAL_SAMPLES["datetime"]
    if name.endswith("_date"):
        return _TEMPORAL_SAMPLES["date"]
    if name.endswith("_time") or name == "time":
        return _TEMPORAL_SAMPLES["time"]
    return None


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
    if _looks_like_email_field(field):
        return "guest@example.com"
    temporal = _temporal_sample(field)
    if temporal is not None:
        return temporal
    name = str(field.get("name") or "").lower()
    declared = str(field.get("type") or "str").strip().lower()
    # Name-only fallback when the spec still says str but the handler
    # (VetConnect) demanded a bool / count. Alignment usually upgrades the
    # type first; this keeps the sample honest if a stale spec slips through.
    if declared in ("str", "text", "string", ""):
        if name.startswith("is_") or name.startswith("has_"):
            return True
        if name.endswith("_count") or name in {"capacity", "quantity", "login_count"}:
            return 1
    ftype = _normalize_field_type(field.get("type") or "str")
    if ftype in ("int", "float"):
        lo, hi = field.get("min"), field.get("max")
        if lo is not None:
            # min=0 is a valid bound; 0 is also falsy. LLM handlers often
            # write ``if not payload.get("capacity")`` so prefer a positive
            # sample that still satisfies the bound.
            if lo <= 0 and (hi is None or hi >= 1):
                return 1 if ftype == "int" else 1.0
            return lo
        if hi is not None:
            return hi if hi < _SAMPLE_VALUES[ftype] else _SAMPLE_VALUES[ftype]
    if ftype == "str" and (name.endswith("_id") or name.endswith("_code")):
        return "id-1"
    return _SAMPLE_VALUES.get(ftype, "sample")


def _sample_payload(spec: Dict[str, Any]) -> Dict[str, Any]:
    """A valid instance of the entity, built from its own spec.

    Includes every declared field (required and optional) so pilot
    ``test_every_capability_route_accepts_payload`` never omits a column the
    route/handler guard demands. Values honour vocabulary, bounds, and
    email-shaped names via ``_sample_value``.
    """
    out: Dict[str, Any] = {}
    for field in spec.get("fields", []) or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        if "type" not in field:
            field = {**field, "type": "str"}
        out[str(field["name"])] = _sample_value(field)
    return out


def _field_default(field: Dict[str, Any]) -> str:
    """Python literal for the dataclass default, valid under the constraints."""
    if field.get("allowed_values"):
        return repr(field["allowed_values"][0])
    ftype = _normalize_field_type(field.get("type") or "str")
    if ftype in ("int", "float") and field.get("min") is not None:
        return repr(field["min"])
    temporal = _temporal_sample(field)
    if temporal is not None:
        return repr(temporal)
    return _PY_DEFAULTS.get(ftype, '""')


def _constraints_of(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for f in spec.get("fields", []):
        c = {
            k: f[k]
            for k in ("allowed_values", "min", "max", "format")
            if f.get(k) is not None
        }
        if c:
            out[f["name"]] = c
    return out


def run_tester(ctx: RoleContext) -> RoleResult:
    """Acceptance inspector: write the code-phase suite. tests/ only.

    Factory-gated tests judge the coder's 20–30 minute pass: imports,
    dispatch load, handle() returning a mapping, model round-trip, routes
    answering HTTP 200 JSON. Store-backed ``ok: True`` and nested-error
    scans are ``@pytest.mark.pilot`` — a complete platform as designed is
    a later phase, not this gate.

    Extra coding-agent cases are mutations of spec payloads. They are
    written as ``tests/agent_domain_cases.py`` so pytest does not collect
    them. GET /v1/gates describes this coverage; it does not run the suite.

    On a pilot cycle the suite is already on disk. Rewriting it against an
    empty in-memory spec (worker restart / first pilot TESTER pass) would
    change payloads and hide the agent's own cases — keep the files then.

    On pilot *rework* (``work_list`` set after a red PRODUCT suite) WRITER
    may have regenerated handlers and ``model_specs``. Keeping the frozen
    code-phase payloads then burns the rework budget against stale probes.
    Re-emit the suite from the current specs so ``pytest -m pilot`` matches
    the workspace under test.
    """
    if str(ctx.state.get("build_cycle") or "") == "pilot":
        existing = Path("tests") / "test_smoke.py"
        if ctx.workspace.exists(existing) and not ctx.work_list:
            return RoleResult(
                ok=True,
                detail=(
                    "pilot cycle: existing suite kept; gate will run pytest -m pilot"
                ),
                vendored_blocks=tuple(sorted(set(ctx.state.get("vendored_blocks", ())))),
            )
    caps = [c.capability_id.replace("-", "_") for c in ctx.plan.capabilities]
    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
    specs = {
        cid: dict(spec) if isinstance(spec, dict) else spec
        for cid, spec in dict(ctx.state.get("model_specs") or {}).items()
    }
    # Late align: coder rework may have rewritten handlers after WRITER's
    # first align, or left vocab/type on the handler that the spec never
    # declared. Re-mine on-disk handlers so _sample_payload matches the
    # body TESTER is about to exercise (live VetConnect miss).
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        handler_rel = Path("app") / "actions" / f"{cid.replace('-', '_')}.py"
        if not ctx.workspace.exists(handler_rel):
            continue
        specs[cid], _changed = align_spec_to_handler_source(
            specs.get(cid) or {},
            ctx.workspace.read_text(handler_rel),
        )
        specs[cid], _env = ensure_record_envelope(specs[cid])
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
        "import pytest",
        "",
        "",
        "def test_capabilities_import():",
    ]
    for name in caps:
        smoke.append(f"    from app.actions import {name}")
        smoke.append(f"    assert {name}.CAPABILITY_ID")
    if not caps:
        smoke.append("    pass")
    default_actions = {
        b: contract["default_action"]
        for b in vendored
        if (contract := _block_contract(ctx, b)).get("default_action")
    }
    smoke += [
        "",
        "",
        "def test_dispatch_runs_offline():",
        '    """No store env, no network: every block must LOAD and EXECUTE from',
        "    vendor/. A block-level refusal of this bare probe is still local",
        "    execution; an import failure is the store dependency this test",
        '    exists to catch."""',
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):',
        "        os.environ.pop(var, None)",
        "    from app.dispatch import execute, load_block",
        f"    for block_id in {vendored!r}:",
        "        load_block(block_id)",
        f"    actions = {default_actions!r}",
        f"    for block_id in {vendored!r}:",
        "        try:",
        "            result = execute(block_id, {}, action=actions.get(block_id))",
        "        except RuntimeError as exc:",
        "            # The block ran and refused the empty probe -- fine here;",
        "            # the pilot test below demands Store-backed success.",
        "            # An import error is never fine.",
        '            assert "No module named" not in str(exc), (block_id, exc)',
        '            assert "cannot import" not in str(exc), (block_id, exc)',
        "        else:",
        "            assert isinstance(result, dict), block_id",
    ]
    if vendored:
        smoke += [
            "",
            "",
            "def test_kit_packs_present():",
            '    """The download is a product tree: kits/ next to vendor/blocks."""',
            "    from pathlib import Path as _Path",
            '    kits = _Path(__file__).resolve().parents[1] / "kits"',
            '    assert kits.is_dir(), "kits/ missing from the delivered platform"',
            '    assert list(kits.glob("*/manifest.json")), "no kit pack manifests"',
        ]
    smoke += [
        "",
        "",
        "def test_every_capability_handle_returns_mapping():",
        '    """Code-phase: the coder wired handle() and it returns a dict.',
        "    Store ok: False or a Store exception is not this gate.\"\"\"",
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):',
        "        os.environ.pop(var, None)",
        "    failures = []",
    ]
    for cap in ctx.plan.capabilities:
        name = cap.capability_id.replace("-", "_")
        sample = _sample_payload(specs.get(cap.capability_id, {}))
        smoke += [
            f"    from app.actions import {name}",
            "    try:",
            f"        out = {name}.handle({sample!r})",
            "    except Exception as exc:",
            "        out = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}",
            "    if not isinstance(out, dict):",
            f"        failures.append('{name} handle() must return a dict, got '"
            " + type(out).__name__)",
        ]
    if caps:
        smoke.append('    assert not failures, "; ".join(failures)')
    else:
        smoke.append("    pass")
    smoke += [
        "",
        "",
        "@pytest.mark.pilot",
        "def test_every_capability_executes_end_to_end():",
        '    """Pilot: each handler runs its blocks on a spec payload and',
        "    Store must accept it. Not the factory code-phase gate.\"\"\"",
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):',
        "        os.environ.pop(var, None)",
        "    import json as _json",
        "    failures = []",
    ]
    for cap in ctx.plan.capabilities:
        name = cap.capability_id.replace("-", "_")
        sample = _sample_payload(specs.get(cap.capability_id, {}))
        # Collect, never abort: a run that stops at the first failing
        # capability hides the rest. The nested scan is the fake-green
        # stop: a handler that reports ok around a failed block call is
        # caught here no matter who wrote it.
        smoke += [
            f"    from app.actions import {name}",
            f"    out = {name}.handle({sample!r})",
            "    if not isinstance(out, dict):",
            f"        failures.append('{name} returned a non-dict: ' + repr(out)[:120])",
            '    elif out.get("ok") is False:',
            f"        failures.append('{name} rejected a payload built from its own "
            "schema: ' + str(out.get('error')))",
            "    elif '\\\"status\\\": \\\"error\\\"' in _json.dumps(out) or "
            "'\\\"status\\\": \\\"failed\\\"' in _json.dumps(out):",
            f"        failures.append('{name} reported ok around a failed block "
            "call: ' + _json.dumps(out)[:300])",
        ]
    if caps:
        smoke.append('    assert not failures, "; ".join(failures)')
    else:
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
    from app.factory.build.data_lifecycle import render_product_tests

    ctx.workspace.write_text(
        Path("tests") / "test_data_lifecycle.py", render_product_tests(specs)
    )
    from app.factory.build.deploy import render_product_tests as render_deploy_tests
    from app.factory.build.domain_acceptance import (
        render_product_tests as render_domain_tests,
    )

    ctx.workspace.write_text(
        Path("tests") / "test_deploy.py", render_deploy_tests(specs)
    )
    ctx.workspace.write_text(
        Path("tests") / "test_domain_acceptance.py", render_domain_tests(specs)
    )

    # -- routes return their documented shape ------------------------------
    route_lines = [
        '"""The HTTP surface answers, and what it answers has the right shape."""',
        "",
        "import pytest",
        "from fastapi.testclient import TestClient",
        "",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        "",
        "",
        "def _listed(payload):",
        '    """Same list shapes as PRODUCT round-trip (_listed / _listed_records).',
        "",
        "    Hard-coding listed.json()['items'] KeyError'd when GET answered",
        "    {ok: False} or {records: [...]} — pytest then reported only",
        "    'suite is red' with no capability id (sess_5dfb4a3 class).",
        '    """',
        "    if isinstance(payload, list):",
        "        return payload",
        "    if not isinstance(payload, dict):",
        "        return []",
        '    if payload.get("ok") is False:',
        "        return []",
        '    for key in ("items", "records", "results", "data", "rows"):',
        "        value = payload.get(key)",
        "        if isinstance(value, list):",
        "            return value",
        "    return []",
        "",
        "",
        "def test_health():",
        '    resp = client.get("/health")',
        "    assert resp.status_code == 200",
        "    body = resp.json()",
        '    assert body["status"] == "ok"',
        '    assert body["ok"] is True',
        '    names = {item["name"] for item in body["checks"]}',
        '    assert {"process", "persistent_disk", "database", "migrations"} <= names',
        "    assert all(item[\"ok\"] for item in body[\"checks\"])",
        "",
        "",
        "def test_kernel_jobs_roster():",
        '    """GET /v1/jobs publishes every kernel JD; distinctive routes answer."""',
        '    resp = client.get("/v1/jobs")',
        "    assert resp.status_code == 200",
        '    jobs = resp.json()["jobs"]',
        '    by_kernel = {j["kernel"]: j for j in jobs}',
        '    assert set(by_kernel) == {',
        '        "COLLECTOR", "CLONER", "WRITER", "TESTER", "STORE_MANAGER"',
        "    }",
        '    assert by_kernel["COLLECTOR"]["title"] == "Binding surveyor"',
        '    assert by_kernel["CLONER"]["title"] == "Block stocker"',
        '    assert by_kernel["WRITER"]["title"] == "Platform manufacturer"',
        '    assert by_kernel["TESTER"]["title"] == "Acceptance inspector"',
        '    assert by_kernel["STORE_MANAGER"]["title"] == "Store registrar"',
        "    for job in jobs:",
        '        assert job["mandate"] and job["http_routes"] and job["agent"]',
        '    catalog = client.get("/v1/catalog")',
        "    assert catalog.status_code == 200",
        '    assert catalog.json()["kernel"] == "COLLECTOR"',
        '    inventory = client.get("/v1/inventory")',
        "    assert inventory.status_code == 200",
        '    assert inventory.json()["kernel"] == "CLONER"',
        '    assert "lock" in inventory.json()',
        '    caps_resp = client.get("/v1/capabilities")',
        "    assert caps_resp.status_code == 200",
        '    assert isinstance(caps_resp.json()["items"], list)',
        '    gates = client.get("/v1/gates")',
        "    assert gates.status_code == 200",
        '    assert gates.json()["kernel"] == "TESTER"',
        '    assert gates.json()["runs_over_http"] is False',
        '    prov = client.get("/v1/provenance")',
        "    assert prov.status_code == 200",
        '    assert prov.json()["kernel"] == "STORE_MANAGER"',
        "",
        "",
        "def test_every_capability_route_answers():",
        '    """Code-phase: each capability POST answers HTTP 200 JSON.',
        "    Store ok: False is allowed here — acceptance is the pilot test.\"\"\"",
    ]
    if caps:
        route_lines.append("    failures = []")
        for cap in ctx.plan.capabilities:
            name = cap.capability_id.replace("-", "_")
            spec = specs.get(cap.capability_id, {})
            sample = _sample_payload(spec)
            route_lines += [
                f"    payload = {sample!r}",
                f'    resp = client.post("/v1/{name}", json=payload)',
                "    if resp.status_code != 200:",
                f"        failures.append('{name}: HTTP ' + str(resp.status_code)"
                " + ': ' + resp.text[:200])",
                "    else:",
                "        try:",
                "            body = resp.json()",
                "        except Exception:",
                f"            failures.append('{name}: response is not JSON')",
                "        else:",
                "            if not isinstance(body, dict):",
                f"                failures.append('{name}: JSON body is not a dict')",
                f'            listed = client.get("/v1/{name}")',
                "            if listed.status_code != 200:",
                f"                failures.append('{name} list: HTTP '"
                " + str(listed.status_code))",
                "",
            ]
        route_lines.append('    assert not failures, "; ".join(failures)')
    else:
        route_lines.append("    pass")
    route_lines += [
        "",
        "",
        "@pytest.mark.pilot",
        "def test_every_capability_route_accepts_payload():",
        '    """Pilot: spec payload is accepted (ok is not False) and persisted.',
        "    Not the factory code-phase gate.\"\"\"",
    ]
    if caps:
        route_lines.append("    failures = []")
        for cap in ctx.plan.capabilities:
            name = cap.capability_id.replace("-", "_")
            spec = specs.get(cap.capability_id, {})
            sample = _sample_payload(spec)
            route_lines += [
                f"    payload = {sample!r}",
                f'    resp = client.post("/v1/{name}", json=payload)',
                "    if resp.status_code != 200:",
                f"        failures.append('{name}: HTTP ' + str(resp.status_code)"
                " + ': ' + resp.text[:200])",
                '    elif resp.json().get("ok") is False:',
                f"        failures.append('{name} rejected a payload built from its "
                "own schema: ' + str(resp.json().get('error')))",
                "    else:",
                f'        listed = client.get("/v1/{name}")',
                "        listed_body = listed.json() if listed.content else {}",
                "        if listed.status_code != 200:",
                f"            failures.append('{name} list: HTTP '"
                " + str(listed.status_code))",
                '        elif isinstance(listed_body, dict) and '
                'listed_body.get("ok") is False:',
                f"            failures.append('{name} list refused: '"
                " + str(listed_body.get('error') or listed_body)[:200])",
                "        else:",
                "            rows = _listed(listed_body)",
                "            if not rows:",
                f"                failures.append('{name} accepted a record but "
                "persisted nothing')",
                "            else:",
                '                item_id = rows[0].get("id")',
                f'                got = client.get(f"/v1/{name}/{{item_id}}")',
                "                if got.status_code != 200:",
                f"                    failures.append('{name} get: HTTP '"
                " + str(got.status_code))",
                f'                missing = client.get("/v1/{name}/999999")',
                "                if missing.status_code != 404:",
                f"                    failures.append('{name} missing id: HTTP '"
                " + str(missing.status_code) + ' (expected 404)')",
                "",
            ]
        route_lines.append('    assert not failures, "; ".join(failures)')
    else:
        route_lines.append("    pass")
    ctx.workspace.write_text(
        Path("tests") / "test_routes.py", "\n".join(route_lines) + "\n"
    )

    admitted, case_model = _tester_agent_cases(ctx, specs)
    if admitted:
        ctx.workspace.write_text(
            Path("tests") / "agent_domain_cases.py",
            _render_agent_domain_tests(admitted),
        )
        ctx.note(
            f"coding agent added {len(admitted)} tester domain case(s)",
            stage="tester",
            cases=len(admitted),
        )

    if str(ctx.state.get("build_cycle") or "") == "pilot" and ctx.work_list:
        detail = (
            f"pilot rework: suite refreshed from model_specs for {len(caps)} "
            "capability(ies); gate will run pytest -m pilot"
        )
    else:
        detail = (
            f"code-phase suite written for {len(caps)} capability(ies): import, "
            "dispatch load, handle() mapping, persistence, route JSON; "
            "Store-backed execute-all is @pytest.mark.pilot"
        )
    if admitted:
        detail += f"; coding agent added {len(admitted)} domain case(s)"
    return RoleResult(
        ok=True,
        detail=detail,
        notes={
            "capabilities": caps,
            "vendored": vendored,
            "entities": entities,
            "agent_domain_cases": admitted,
            "agent_domain_model": case_model,
            "model_specs": specs,
        },
    )


def _payload_constraint_violations(
    payload: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    """Field-constraint breaks, used to admit TESTER agent cases."""
    hits: List[str] = []
    for name, rules in _constraints_of(spec).items():
        if name not in payload:
            continue
        value = payload[name]
        allowed = rules.get("allowed_values")
        if allowed is not None and value not in allowed:
            hits.append(name)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        low, high = rules.get("min"), rules.get("max")
        if low is not None and value < low:
            hits.append(name)
        elif high is not None and value > high:
            hits.append(name)
    return hits


def _is_payload_mutation(sample: Dict[str, Any], proposed: Dict[str, Any]) -> bool:
    """True when proposed is a mutation of the spec-derived sample, not a replacement."""
    if not isinstance(proposed, dict) or not sample:
        return False
    extra = set(proposed) - set(sample)
    if extra:
        return False
    if not proposed:
        return False
    return proposed != sample


def _tester_agent_cases(
    ctx: RoleContext, specs: Dict[str, Any]
) -> tuple:
    """Ask the coding agent for extra domain cases; admit only valid mutations."""
    from app.factory.coder import CoderError, coder_enabled, propose_domain_test_cases

    if not coder_enabled():
        return [], ""
    if _budget_too_low(ctx, "tester domain cases"):
        return [], ""
    samples = []
    sample_by_id: Dict[str, Dict[str, Any]] = {}
    for cap in ctx.plan.capabilities:
        spec = specs.get(cap.capability_id, {})
        sample = _sample_payload(spec)
        sample_by_id[cap.capability_id] = sample
        samples.append(
            {
                "id": cap.capability_id,
                "description": getattr(cap, "notes", "") or cap.capability_id,
                "sample_payload": sample,
            }
        )
    try:
        result = propose_domain_test_cases(
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
            capabilities=samples,
        )
    except (CoderError, Exception) as exc:  # noqa: BLE001 -- extras are optional
        ctx.state.setdefault("coder_failures", {})["TESTER.domain_cases"] = str(exc)
        ctx.note(f"coding agent skipped tester domain cases: {exc}", stage="tester")
        return [], ""

    admitted: List[Dict[str, Any]] = []
    for case in result.get("cases") or []:
        cap_id = case.get("capability_id")
        sample = sample_by_id.get(cap_id) or {}
        payload = case.get("payload") or {}
        if not _is_payload_mutation(sample, payload):
            continue
        spec = specs.get(cap_id) or {}
        violations = _payload_constraint_violations(payload, spec)
        expect = case.get("expect")
        # Only admit reject-cases the kernel can actually enforce, and
        # accept-cases that satisfy the spec. Live TESTER burned three
        # rework rounds on "revenue cannot be negative" when no field min
        # existed, so the route accepted the payload.
        if expect == "reject" and not violations:
            continue
        if expect != "reject" and violations:
            continue
        admitted.append(case)
    return admitted, str(result.get("model") or "")


def _render_agent_domain_tests(cases: List[Dict[str, Any]]) -> str:
    """Kernel-owned file of agent-proposed cases. TESTER writes this, not WRITER."""
    lines = [
        '"""Additional domain cases proposed by the coding agent under TESTER.',
        "",
        "These are mutations of spec-derived payloads. They cannot replace the",
        "kernel suite in test_routes.py / test_models.py. The file is named",
        "agent_domain_cases.py (no test_ prefix) so pytest does not collect it",
        "into the gated suite.",
        '"""',
        "",
        "from fastapi.testclient import TestClient",
        "",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        "",
        "",
        "def test_agent_domain_cases():",
        "    failures = []",
    ]
    for i, case in enumerate(cases):
        name = str(case["capability_id"]).replace("-", "_")
        payload = case["payload"]
        expect = case["expect"]
        reason = (case.get("reason") or "").replace("\\", "\\\\").replace("'", "\\'")
        lines += [
            f"    payload_{i} = {payload!r}",
            f'    resp_{i} = client.post("/v1/{name}", json=payload_{i})',
        ]
        if expect == "reject":
            lines += [
                f"    if resp_{i}.status_code == 200 and resp_{i}.json().get('ok') is not False:",
                f"        failures.append({reason!r} or 'case {i} should have been rejected')",
            ]
        else:
            lines += [
                f"    if resp_{i}.status_code != 200 or resp_{i}.json().get('ok') is False:",
                f"        failures.append({reason!r} or 'case {i} should have been accepted')",
            ]
    lines += [
        "    assert not failures, '; '.join(failures)",
        "",
    ]
    return "\n".join(lines) + "\n"


# -- STORE_MANAGER -------------------------------------------------------


def run_store_manager(ctx: RoleContext) -> RoleResult:
    """Store registrar: register what this build took from the Store.

    Code-phase 5/5 records the clone register and applies no Store op
    (historical). The pilot cycle applies authorised ``STORE_READ`` for
    every clone. A live Store URL additionally runs
    ``STORE_RUN_COMPATIBILITY``. When ``CEREBRUM_API_URL`` is unset the
    gate still passes with an honest ``store_unwired`` flag — local reads
    only. No agent. Published on the product as ``GET /v1/provenance``.
    """
    import os

    from app.factory.store_manager import StoreOp, assert_store_op_allowed

    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
    cycle = str(ctx.state.get("build_cycle") or "code")
    ops: list = []
    store_unwired = False
    if cycle == "pilot":
        for bid in vendored:
            assert_store_op_allowed(StoreOp.STORE_READ)
            ops.append(
                {
                    "op": StoreOp.STORE_READ.value,
                    "block_id": bid,
                    "source": "clone_register",
                }
            )
        store_unwired = not (os.getenv("CEREBRUM_API_URL") or "").strip()
        if not store_unwired:
            assert_store_op_allowed(StoreOp.STORE_RUN_COMPATIBILITY)
            ops.append(
                {
                    "op": StoreOp.STORE_RUN_COMPATIBILITY.value,
                    "source": "store_url",
                }
            )
        detail = f"registered {len(vendored)} clone(s); applied {len(ops)} store op(s)"
        if store_unwired:
            detail += "; store unwired — local STORE_READ only"
    else:
        detail = f"registered {len(vendored)} clone(s); no store op applied"
    return RoleResult(
        ok=True,
        detail=detail,
        vendored_blocks=tuple(vendored),
        notes={
            "registered": vendored,
            "store_ops": ops,
            "store_unwired": store_unwired,
        },
    )


ROLE_IMPLEMENTATIONS = {
    BuildRole.COLLECTOR: run_collector,
    BuildRole.CLONER: run_cloner,
    BuildRole.WRITER: run_writer,
    BuildRole.TESTER: run_tester,
    BuildRole.STORE_MANAGER: run_store_manager,
}
