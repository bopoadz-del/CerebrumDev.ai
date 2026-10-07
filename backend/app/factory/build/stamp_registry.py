"""Every Factory stamp that writes into a product tree -- ONE registry.

Rule (owner, 2026-10-07): a Factory stamp NEVER re-renders a product-authored
file. Each stamp is classed by what it may do to the paths it touches:

* ``OWNED``  -- the path is the Factory's outright (the product never authors
  it; the writer's lane excludes it). Whole-file stamp, every pass.
* ``SHARED`` -- the product and the Factory both write the file; the stamp
  edits ONLY its marked block (factory_block) and every other byte stays.
* ``GAP``    -- substrate the Factory writes only where the product left a
  gap; anything already there is left exactly as written. (The D2 stub repair
  replaces only a file that provides NONE of the names the Factory's own
  stamped suite imports -- substrate the agent is never asked for.)

Ownership is read from here and nowhere else: the factory receipt's
``factory_stamped_paths`` is the OWNED paths below, and the structural test
(tests/factory/test_stamps_never_rewrite_product_bytes.py) enumerates this
table -- it never keeps a list of its own.

Stamps that run only on the template path (where the Factory authors the
whole product: ``stamp_acceptance_artifacts``, ``render_routes_files``) and
the Store gate's own workflow (pushed from cerebrum-builds ``main`` by
builds_push, never a product file) do not touch a product-authored tree and
are not listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, Tuple

OWNED = "owned"
SHARED = "shared"
GAP = "gap"


@dataclass(frozen=True)
class Stamp:
    name: str
    kind: str
    paths: Tuple[str, ...]
    #: Applies the stamp to a workspace root (the structural test drives it);
    #: None for a stamp only the runner can drive (TESTER's suites).
    apply: Optional[Callable[[Path], Any]] = None


class _Workspace:
    """The workspace surface the stamps write through."""

    def __init__(self, root: Path):
        self.workspace = Path(root)

    def _p(self, rel: Any) -> Path:
        return self.workspace / Path(rel)

    def exists(self, rel: Any) -> bool:
        return self._p(rel).exists()

    def read_text(self, rel: Any) -> str:
        return self._p(rel).read_text(encoding="utf-8")

    def write_text(self, rel: Any, text: str) -> Path:
        path = self._p(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def __truediv__(self, rel: Any) -> Path:
        return self._p(rel)


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=_Workspace(root),
        state={},
        plan=SimpleNamespace(capabilities=()),
        blueprint=None,
    )


def _roster(root: Path) -> Any:
    from app.factory.build.kernel_publish import stamp_roster

    return stamp_roster(_ctx(root))


def _deploy_modules(root: Path) -> Any:
    from app.factory.build.deploy import stamp_factory_deploy_modules

    return stamp_factory_deploy_modules(_Workspace(root))


def _acceptance(root: Path) -> Any:
    from app.factory.build.store_acceptance import stamp_acceptance_harness

    return stamp_acceptance_harness(_Workspace(root))


def _self_check(root: Path) -> Any:
    from app.factory.build.writer_behaviour import emit_self_check

    return emit_self_check(_Workspace(root))


def _refresh(root: Path) -> Any:
    from app.factory.build.factory_refresh import refresh_factory_files

    return refresh_factory_files(root, "platform")


def _platform_gap(root: Path) -> Any:
    from app.factory.build.data_lifecycle import backfill_platform_substrate

    return backfill_platform_substrate(_Workspace(root))


def _deploy_gap(root: Path) -> Any:
    from app.factory.build.deploy import backfill_deploy_substrate

    return backfill_deploy_substrate(_Workspace(root))


def stamps() -> Tuple[Stamp, ...]:
    """The table, built from each stamp's own path constants."""
    from app.factory.build.data_lifecycle import platform_substrate
    from app.factory.build.deploy import FACTORY_OWNED_DEPLOY_MODULES, deploy_substrate
    from app.factory.build.kernel_publish import JOBS_REL
    from app.factory.build.placeholder_connectors import CONTRACT_TEST
    from app.factory.build.product_suites import PRODUCT_SUITES
    from app.factory.build.store_acceptance import ACCEPTANCE_SCRIPT_REL, GITHUB_CI_REL
    from app.factory.build.writer_behaviour import SELF_CHECK_REL

    def rel(p: Any) -> str:
        return str(p).replace("\\", "/")

    deploy_owned = tuple(rel(p) for p in FACTORY_OWNED_DEPLOY_MODULES)
    return (
        Stamp("kernel roster", SHARED, (rel(JOBS_REL),), _roster),
        Stamp("deploy modules", OWNED, deploy_owned, _deploy_modules),
        Stamp("acceptance harness", OWNED, (rel(ACCEPTANCE_SCRIPT_REL),), _acceptance),
        Stamp("writer self-check", OWNED, (rel(SELF_CHECK_REL),), _self_check),
        Stamp(
            "re-entry refresh (Factory files)",
            OWNED,
            ("scripts/release_gate.py", rel(ACCEPTANCE_SCRIPT_REL), rel(SELF_CHECK_REL), rel(GITHUB_CI_REL)),
            _refresh,
        ),
        Stamp("re-entry refresh (requirements)", SHARED, ("requirements.txt",), _refresh),
        Stamp(
            "TESTER product suites",
            OWNED,
            tuple(dict.fromkeys((*PRODUCT_SUITES, rel(CONTRACT_TEST), "tests/test_data_lifecycle.py",
                                 "tests/test_deploy.py"))),
        ),
        Stamp("platform substrate (gaps)", GAP, tuple(rel(p) for p, _t in platform_substrate()), _platform_gap),
        Stamp(
            "deploy substrate (gaps)",
            GAP,
            tuple(rel(p) for p, _t in deploy_substrate() if rel(p) not in deploy_owned),
            _deploy_gap,
        ),
    )


def owned_paths() -> Tuple[str, ...]:
    """Every path a Factory stamp owns outright (the receipt's source)."""
    seen: dict = {}
    for stamp in stamps():
        if stamp.kind == OWNED:
            for p in stamp.paths:
                seen.setdefault(p, None)
    return tuple(seen)


def shared_paths() -> Tuple[str, ...]:
    return tuple(dict.fromkeys(p for s in stamps() if s.kind == SHARED for p in s.paths))
