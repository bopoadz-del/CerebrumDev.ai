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

    for bid in block_ids:
        source = _block_source_dir(bid, ctx.blocks_root)
        if source is None:
            missing.append(bid)
            continue
        ctx.workspace.copy_tree(source, Path("vendor") / "blocks" / bid)
        origin = (
            "cerebrum-blocks"
            if ctx.blocks_root and str(source).startswith(str(ctx.blocks_root))
            else "factory-vendor-mirror"
        )
        lock["blocks"][bid] = {"source": origin, "path": f"vendor/blocks/{bid}"}
        vendored.append(bid)

    if missing:
        raise RoleError(
            "no source found for block(s): " + ", ".join(sorted(missing))
        )

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


def execute(block_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run a vendored block locally and return its result."""
    module = load_block(block_id)
    run = getattr(module, "run", None)
    if run is None:
        raise BlockNotVendored(f"{block_id} exposes no run() entry point")
    return run(input=payload)
'''


def _handler_module(capability_id: str, block_ids: Sequence[str], body: str, source: str) -> str:
    return f'''"""Handler for capability {capability_id}.

Written by the factory WRITER role ({source}). Blocks are invoked through the
local dispatch runtime -- this module makes no network call.
"""

from __future__ import annotations

from typing import Any, Dict

from app.dispatch import execute

CAPABILITY_ID = "{capability_id}"
BLOCK_IDS = {list(block_ids)!r}


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
        "        results[block_id] = execute(block_id, payload)\n"
        '    return {"capability": CAPABILITY_ID, "status": "ok", "results": results}'
    )


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
        )
    except CoderError as exc:
        # Degraded output is acceptable; invisible degradation is not.
        ctx.state.setdefault("coder_failures", {})[cap.capability_id] = str(exc)
        return None
    return result["body"], f"coder LLM ({result['model']})"


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

        ctx.workspace.write_text(
            Path("app") / "actions" / f"{name}.py",
            _handler_module(cap.capability_id, usable, body, source),
        )
        actions_init.append(f"from app.actions import {name}  # noqa: F401")
        written.append(name)
        sources[cap.capability_id] = source

    ctx.workspace.write_text(
        Path("app") / "actions" / "__init__.py", "\n".join(actions_init) + "\n"
    )

    by_coder = sum(1 for s in sources.values() if s.startswith("coder LLM"))
    detail = (
        f"{len(written)} handler(s) written — {by_coder} by the coding agent, "
        f"{len(written) - by_coder} from the contract template"
    )
    if ctx.work_list:
        detail += f"; reworking {len(ctx.work_list)} finding(s)"
    return RoleResult(
        ok=True,
        detail=detail,
        notes={
            "handlers": written,
            "handler_sources": sources,
            "coder_handlers": by_coder,
        },
    )


# -- TESTER --------------------------------------------------------------


def run_tester(ctx: RoleContext) -> RoleResult:
    """Write tests against what the WRITER produced. Writes only under tests/.

    The smoke test executes a capability through the local dispatch runtime
    with no store configured, which is the assertion the old generated
    platforms shipped but could not honour.
    """
    caps = [c.capability_id.replace("-", "_") for c in ctx.plan.capabilities]
    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))

    lines = [
        '"""Smoke tests for the generated platform (written by the TESTER role)."""',
        "",
        "import os",
        "import sys",
        "from pathlib import Path",
        "",
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))",
        "",
        "",
        "def test_capabilities_import():",
    ]
    for name in caps:
        lines.append(f"    from app.actions import {name}")
        lines.append(f"    assert {name}.CAPABILITY_ID")
    if not caps:
        lines.append("    pass")

    lines += [
        "",
        "",
        "def test_dispatch_runs_offline():",
        '    """No store env, no network: blocks must run from vendor/."""',
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):',
        "        os.environ.pop(var, None)",
        "    from app.dispatch import execute",
        f"    vendored = {vendored!r}",
        "    for block_id in vendored:",
        "        result = execute(block_id, {'probe': True})",
        "        assert isinstance(result, dict), block_id",
        "    assert True",
    ]

    ctx.workspace.write_text(Path("tests") / "test_smoke.py", "\n".join(lines) + "\n")
    return RoleResult(
        ok=True,
        detail=f"smoke suite written for {len(caps)} capability(ies)",
        notes={"capabilities": caps, "vendored": vendored},
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
