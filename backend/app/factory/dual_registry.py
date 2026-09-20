"""Dual registration gate: Cerebrum-Blocks + Factory shelf must both list a block."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


class DualRegistryError(Exception):
    """Raised when a block is missing from Blocks or the Factory shelf."""


@dataclass(frozen=True)
class BlockRef:
    block_id: str
    version: str
    source: str
    #: Who vouches for this block. Empty means nobody has -- which is the
    #: state ``compliance_gate`` refuses, not a synonym for "fine". Only the
    #: Factory shelf carries tiers today; refs from the Blocks registry leave
    #: it empty, and the gate only consults the shelf.
    trust_tier: str = ""


def _default_blocks_root() -> Path:
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    # /workspace/backend/app/factory -> try sibling repos
    from app.factory.paths import factory_repo_root

    repo = factory_repo_root()
    candidates = [
        repo.parent / "Cerebrum-Blocks",
        here.parents[3] / "Cerebrum-Blocks",
        Path("../Cerebrum-Blocks").resolve(),
    ]
    for c in candidates:
        if c.exists():
            return c
    # Production has neither the env var nor a sibling checkout: it builds
    # from the pinned Store clone (engine_discovery). This function never
    # looked there -- the vendor mirror silently covered for it -- so once
    # the mirror was deleted the architect saw an EMPTY Store and drafted
    # every capability as GENERATE with no blocks (live, 2026-09-19). CI
    # could not see it because CI sets CEREBRUM_BLOCKS_ROOT.
    try:
        from app.factory.blocks_source import resolve_blocks_root

        resolved = resolve_blocks_root()
        if resolved is not None and (Path(resolved) / "block_registry").is_dir():
            return Path(resolved)
    except Exception:  # noqa: BLE001 -- no Store is reported by the caller, not here
        pass
    return candidates[0]


def _factory_shelf_path() -> Path:
    return Path(__file__).resolve().parent / "shelves" / "factory_blocks.json"


def shelf_from_store(blocks_root: Optional[Path] = None) -> Dict[str, BlockRef]:
    """The shelf, resolved from what the Store publishes about itself.

    Every block_registry/<id>/block.json carries id, version and trust_tier --
    the Store's own statement about the block. The Factory used to keep a
    hand-written copy of 25 of them in shelves/factory_blocks.json, so it
    offered 25 of the Store's 136 blocks and one of its 19 available kits:
    FinOps was told no finance kit existed while block_store/kits/finance_ops
    sat there marked available, and the agent invented a chart of accounts
    that the Store ships a governance block for.

    Nothing is hardwired here. An unreadable Store yields an empty shelf and
    the caller falls back.
    """
    root = Path(blocks_root) if blocks_root else _default_blocks_root()
    registry = root / "block_registry"
    out: Dict[str, BlockRef] = {}
    if not registry.is_dir():
        return out
    for entry in sorted(registry.iterdir()):
        meta = entry / "block.json"
        if not entry.is_dir() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bid = str(data.get("id") or entry.name).strip()
        if not bid:
            continue
        out[bid] = BlockRef(
            block_id=bid,
            version=str(data.get("version") or "0"),
            source="cerebrum-blocks",
            # The Store's own word on who vouches for it. Empty stays empty:
            # compliance_gate refuses that, and it is not ours to invent.
            trust_tier=str(data.get("trust_tier") or "").strip(),
        )
    return out


def load_factory_shelf(path: Optional[Path] = None) -> Dict[str, BlockRef]:
    """What the Factory has CLEARED to attach -- not what the Store holds.

    These are two different questions and they used to share one file. The
    Store publishes 136 blocks, all trust_tier "platform"; nothing in it says
    which are cleared for a customer build, so clearance stays the Factory's
    statement until the Store publishes one. ``shelf_from_store`` answers the
    other question -- what exists -- and the catalog reports both, so the chat
    can say "it is in the Store, it is not cleared" instead of pretending it
    does not exist.
    """
    p = path or _factory_shelf_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[str, BlockRef] = {}
    for item in data.get("blocks", []):
        bid = item["id"]
        out[bid] = BlockRef(
            block_id=bid,
            version=item.get("version", "0"),
            source="factory",
            trust_tier=(item.get("trust_tier") or "").strip(),
        )
    return out


def _load_registry_dir(registry_dir: Path, source: str) -> Dict[str, BlockRef]:
    out: Dict[str, BlockRef] = {}
    if not registry_dir.exists():
        return out
    from app.factory.blocks_lock import _tracked_relpaths

    for entry in sorted(registry_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Tracked-only: an untracked working-tree block dir is not part
        # of the pinned store sha and must not shadow the vendor mirror
        # (Phase 2 §0.2 lock determinism).
        tracked = _tracked_relpaths(entry)
        if tracked is not None and not any(
            p in ("block.py", "block.json") for p in tracked
        ):
            continue
        meta = entry / "block.json"
        if not meta.exists():
            continue
        data = json.loads(meta.read_text(encoding="utf-8"))
        bid = data.get("id") or entry.name
        out[bid] = BlockRef(
            block_id=bid,
            version=str(data.get("version", "0")),
            source=source,
        )
    return out


def load_blocks_registry(blocks_root: Optional[Path] = None) -> Dict[str, BlockRef]:
    """Load Cerebrum-Blocks registry, merging the Factory vendor mirror.

    The vendor mirror (`app/factory/vendor_blocks_mirror`) carries estate blocks
    that must be dual-registered even when the Blocks remote cannot be updated
    from this environment. Canonical upstream remains Cerebrum-Blocks.
    """
    root = blocks_root or _default_blocks_root()
    out = _load_registry_dir(root / "block_registry", "cerebrum-blocks")
    # Kept only so an old checkout that still has the directory behaves; the
    # Factory holds no blocks and this path no longer exists in the repo.
    mirror = Path(__file__).resolve().parent / "vendor_blocks_mirror"
    # Mirror dirs that look like blocks (contain block.json)
    for entry in sorted(mirror.iterdir()) if mirror.exists() else []:
        if not entry.is_dir() or entry.name.endswith("_kit"):
            continue
        meta = entry / "block.json"
        if not meta.exists():
            continue
        data = json.loads(meta.read_text(encoding="utf-8"))
        bid = data.get("id") or entry.name
        out.setdefault(
            bid,
            BlockRef(
                block_id=bid,
                version=str(data.get("version", "0")),
                source="factory-vendor-mirror",
            ),
        )
    return out


def dual_registered_ids(
    blocks_root: Optional[Path] = None,
    factory_shelf: Optional[Path] = None,
) -> Set[str]:
    factory = set(load_factory_shelf(factory_shelf))
    blocks = set(load_blocks_registry(blocks_root))
    return factory & blocks


def assert_dual_registered(
    block_ids: Iterable[str],
    blocks_root: Optional[Path] = None,
    factory_shelf: Optional[Path] = None,
) -> List[str]:
    """Return sorted dual-registered ids; raise if any id is missing either side."""
    factory = load_factory_shelf(factory_shelf)
    blocks = load_blocks_registry(blocks_root)
    missing: List[str] = []
    ok: List[str] = []
    for bid in block_ids:
        in_f = bid in factory
        in_b = bid in blocks
        if in_f and in_b:
            ok.append(bid)
        else:
            sides = []
            if not in_b:
                sides.append("Cerebrum-Blocks")
            if not in_f:
                sides.append("Factory shelf")
            missing.append(f"{bid} missing from: {', '.join(sides)}")
    if missing:
        raise DualRegistryError(
            "UNSUPPORTED: block(s) not dual-registered — " + "; ".join(missing)
        )
    return sorted(ok)
