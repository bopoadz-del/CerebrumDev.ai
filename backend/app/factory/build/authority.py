"""Role authority kernel — build roles have lanes they cannot write outside of.

The factory's manufacturing run is not one free agent with a long prompt. It
is five roles with defined jobs, run in order, each handing a verified
workspace to the next:

    COLLECTOR -> CLONER -> WRITER -> TESTER -> STORE_MANAGER

"Defined jobs" is enforced here in code rather than asked for in a system
prompt, because a prompt instruction is advice and this needs to be a wall.
A two-hour run is a ratchet only if each phase can leave the workspace in
exactly one shape the next phase can verify; a WRITER that can quietly edit
``tests/`` to make them pass, or a TESTER that can patch ``app/`` instead of
reporting a failure, collapses the whole guarantee.

Two roots exist because the STORE_MANAGER is the one role that writes
*outside* the product being built — promoting reusable work back into the
Block Store is its job (see ``app.factory.store_manager`` for the op
vocabulary and approval gates that govern what it may publish).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


class BuildRole(str, Enum):
    COLLECTOR = "COLLECTOR"
    CLONER = "CLONER"
    WRITER = "WRITER"
    TESTER = "TESTER"
    STORE_MANAGER = "STORE_MANAGER"


class LaneRoot(str, Enum):
    """Which tree a lane is anchored to."""

    WORKSPACE = "WORKSPACE"
    STORE = "STORE"


# Order is the build order. Phases run front to back; a role may never run
# before a role ahead of it in this tuple has passed its gate.
BUILD_PHASES: Tuple[BuildRole, ...] = (
    BuildRole.COLLECTOR,
    BuildRole.CLONER,
    BuildRole.WRITER,
    BuildRole.TESTER,
    BuildRole.STORE_MANAGER,
)


class AuthorityError(PermissionError):
    """A role attempted something outside its mandate. Never downgrade to a
    warning — the run must fail loudly rather than produce an artifact whose
    provenance is unknown."""


@dataclass(frozen=True)
class RoleContract:
    role: BuildRole
    mandate: str
    #: (root, glob) pairs, POSIX-style, relative to that root.
    write_lanes: Tuple[Tuple[LaneRoot, str], ...]
    #: Human-readable name of the gate that must pass before handoff.
    gate: str

    def may_write_anything(self) -> bool:
        return bool(self.write_lanes)


ROLE_CONTRACTS: Mapping[BuildRole, RoleContract] = {
    BuildRole.COLLECTOR: RoleContract(
        role=BuildRole.COLLECTOR,
        mandate=(
            "Resolve the blueprint's chosen block ids into contracts, source and "
            "dependencies. Report every capability with no adequate block as an "
            "explicit gap rather than silently planning around it."
        ),
        # Read-only by design: the collector reports, it does not materialise.
        write_lanes=(),
        gate="every referenced block id is dual-registered; gaps enumerated",
    ),
    BuildRole.CLONER: RoleContract(
        role=BuildRole.CLONER,
        mandate=(
            "Materialise the collected parts into the workspace at pinned commits, "
            "with a local dispatch runtime so handlers import blocks instead of "
            "calling the store over HTTP."
        ),
        write_lanes=(
            (LaneRoot.WORKSPACE, "vendor/**"),
            (LaneRoot.WORKSPACE, "blocks.lock.json"),
        ),
        gate="every vendored block imports with no network configured",
    ),
    BuildRole.WRITER: RoleContract(
        role=BuildRole.WRITER,
        mandate=(
            "Manufacture the platform: capability handlers over the vendored "
            "blocks, domain models, persistence, API surface, UI wiring, and "
            "net-new logic for the gaps the collector reported."
        ),
        write_lanes=(
            (LaneRoot.WORKSPACE, "app/**"),
            (LaneRoot.WORKSPACE, "ui/**"),
            # The run and deploy scaffold a delivered platform needs. Named
            # files rather than a root wildcard: the writer produces exactly
            # these and must not be able to drop anything else at the root.
            (LaneRoot.WORKSPACE, "README.md"),
            (LaneRoot.WORKSPACE, "requirements.txt"),
            (LaneRoot.WORKSPACE, "pyproject.toml"),
            (LaneRoot.WORKSPACE, "Dockerfile"),
            (LaneRoot.WORKSPACE, "Procfile"),
            (LaneRoot.WORKSPACE, ".env.example"),
            (LaneRoot.WORKSPACE, ".dockerignore"),
            (LaneRoot.WORKSPACE, "render.yaml"),
            # The clone-and-test contract the customer runs, and the build
            # provenance it audits. Named, not a wildcard, for the same
            # reason as the files above.
            (LaneRoot.WORKSPACE, "scripts/release_gate.py"),
            (LaneRoot.WORKSPACE, "docs/build_provenance.json"),
        ),
        # Deliberately NOT tests/** — a writer that can edit the tests that
        # judge it has no gate at all. Also not vendor/** or blocks.lock.json:
        # rewriting the vendored source would let the writer make its own
        # handlers pass by changing the blocks underneath them.
        gate="workspace imports and type-checks clean",
    ),
    BuildRole.TESTER: RoleContract(
        role=BuildRole.TESTER,
        mandate=(
            "Write and run tests against what the writer produced, and report "
            "failures back for another writer pass. Never patch the code under "
            "test to make a test pass."
        ),
        write_lanes=((LaneRoot.WORKSPACE, "tests/**"),),
        gate="suite green and the smoke test exercises a capability offline",
    ),
    BuildRole.STORE_MANAGER: RoleContract(
        role=BuildRole.STORE_MANAGER,
        mandate=(
            "Keep the store's books and its stock: register what each platform "
            "cloned and at which commit, flag clones gone stale against store "
            "head, harvest proven improvements out of mature platforms, and "
            "admit client-driven net-new capability into inventory."
        ),
        write_lanes=(
            (LaneRoot.STORE, "block_registry/**"),
            (LaneRoot.STORE, "registry.json"),
        ),
        gate="store_manager.assert_store_op_allowed passes for every op applied",
    ),
}

# Paths no role may write under any root. ``.git`` would let a role rewrite
# the provenance the whole build is audited against.
FORBIDDEN_SEGMENTS = frozenset({".git", ".hg", ".svn"})


def role_contract(role: BuildRole | str) -> RoleContract:
    return ROLE_CONTRACTS[BuildRole(role)]


def _resolved_within(path: Path, root: Path) -> Optional[Path]:
    """Return *path* relative to *root*, or None if it escapes.

    Both sides are fully resolved first so that ``..`` segments and symlinks
    pointing out of the tree are caught rather than normalised away.
    """
    try:
        resolved = Path(path).resolve()
        base = Path(root).resolve()
    except OSError:
        return None
    try:
        return resolved.relative_to(base)
    except ValueError:
        return None


def _matches_lane(relative: Path, glob: str) -> bool:
    """Match POSIX-style, so lanes read the same on Windows and Linux.

    ``fnmatch`` treats ``*`` as crossing separators, which is what a ``**``
    lane wants; an exact-file lane like ``blocks.lock.json`` has no wildcard
    and so still only matches itself.
    """
    posix = relative.as_posix()
    if glob.endswith("/**"):
        prefix = glob[:-3]
        return posix == prefix or posix.startswith(prefix + "/")
    return fnmatch.fnmatch(posix, glob)


def assert_write_allowed(
    role: BuildRole | str,
    path: Path | str,
    *,
    workspace: Path | str,
    store_root: Optional[Path | str] = None,
) -> Path:
    """Authorise one write by *role* and return the resolved target.

    Raises ``AuthorityError`` when the target escapes both roots, lands on a
    VCS directory, or falls outside every lane the role owns.
    """
    role = BuildRole(role)
    contract = ROLE_CONTRACTS[role]

    roots: Dict[LaneRoot, Path] = {LaneRoot.WORKSPACE: Path(workspace)}
    if store_root is not None:
        roots[LaneRoot.STORE] = Path(store_root)

    if not contract.write_lanes:
        raise AuthorityError(
            f"{role.value} is read-only by contract and may not write {path}"
        )

    for lane_root, glob in contract.write_lanes:
        base = roots.get(lane_root)
        if base is None:
            # The store root is only supplied for runs that promote upstream;
            # a store lane simply does not apply when it is absent.
            continue
        relative = _resolved_within(Path(path), base)
        if relative is None:
            continue
        if FORBIDDEN_SEGMENTS.intersection(relative.parts):
            raise AuthorityError(
                f"{role.value} may not write inside a version-control directory: {path}"
            )
        if _matches_lane(relative, glob):
            return Path(path).resolve()

    lanes = ", ".join(f"{r.value}:{g}" for r, g in contract.write_lanes)
    raise AuthorityError(
        f"{role.value} may not write {path} — its lanes are [{lanes}]"
    )


def assert_phase_order(role: BuildRole | str, completed: Iterable[BuildRole | str]) -> None:
    """Every earlier phase must have completed before *role* may run."""
    role = BuildRole(role)
    done = {BuildRole(r) for r in completed}
    index = BUILD_PHASES.index(role)
    missing = [p.value for p in BUILD_PHASES[:index] if p not in done]
    if missing:
        raise AuthorityError(
            f"{role.value} cannot start before: {', '.join(missing)}"
        )


def authority_manifest() -> Dict[str, Any]:
    """Serialisable description of the authority model.

    Shipped with the product the same way ``store_manager_manifest`` is, so a
    delivered platform carries the record of which role was permitted to
    write which part of it.
    """
    return {
        "schema_version": "build_authority.v1",
        "phases": [p.value for p in BUILD_PHASES],
        "roles": {
            role.value: {
                "mandate": contract.mandate,
                "gate": contract.gate,
                "write_lanes": [
                    {"root": r.value, "glob": g} for r, g in contract.write_lanes
                ],
                "read_only": not contract.write_lanes,
            }
            for role, contract in ROLE_CONTRACTS.items()
        },
        "forbidden_segments": sorted(FORBIDDEN_SEGMENTS),
    }
