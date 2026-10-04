"""What the Store's kits declare about themselves, read at build time.

The Factory holds no kit facts. Which kit serves a vertical, whether the
operator declared it ready to build against, and which of its blocks are
domain content are fields of the kit's own manifest in the Store:

* domain kits   ``block_store/kits/<kit>/manifest.json``  -- ``serves_verticals``,
  ``build_ready``, ``blocks``;
* reasoning kits ``app/blocks/<kit>/manifest.yaml`` (+ ``invariants.yaml``)
  -- ``serves_verticals``.

A vertical is served by the kit whose id IS the vertical or whose
``serves_verticals`` lists it. Nothing here names a kit or a vertical.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional


def slug(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _root(store_root: Any) -> Optional[Path]:
    if store_root is not None:
        return Path(store_root)
    try:
        from app.factory.blocks_source import resolve_blocks_root

        return Path(resolve_blocks_root())
    except Exception:  # noqa: BLE001 -- no Store means no kit facts, not a crash
        return None


@lru_cache(maxsize=8)
def _domain_kits(root: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted((Path(root) / "block_store" / "kits").glob("*/manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a template, not a kit
        if isinstance(data, dict):
            out[str(data.get("id") or path.parent.name)] = data
    return out


@lru_cache(maxsize=8)
def _reasoning_kits(root: str) -> Dict[str, Dict[str, Any]]:
    import yaml

    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted((Path(root) / "app" / "blocks").glob("*/manifest.yaml")):
        if not (path.parent / "invariants.yaml").is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            out[path.parent.name] = data
    return out


def domain_kits(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    root = _root(store_root)
    return _domain_kits(str(root)) if root else {}


def reasoning_kits(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    root = _root(store_root)
    return _reasoning_kits(str(root)) if root else {}


def serving_kit(vertical: Any, kits: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The kit that declares it serves ``vertical`` (or is named for it)."""
    want = slug(vertical)
    if not want:
        return None
    if want in kits:
        return want
    for kit_id, manifest in kits.items():
        if want in {slug(v) for v in (manifest.get("serves_verticals") or [])}:
            return kit_id
    return None


def domain_blocks(kit_id: str, kits: Dict[str, Dict[str, Any]]) -> FrozenSet[str]:
    """A kit's own domain blocks: its blocks minus the shared core.

    The shared core is whatever most of the Store's domain kits carry (pdf,
    ocr, chat ... today) -- derived from the manifests, never listed.
    """
    lists = [m.get("blocks") for m in kits.values() if isinstance(m.get("blocks"), list)]
    if not lists:
        return frozenset()
    counts: Dict[str, int] = {}
    for blocks in lists:
        for b in set(blocks):
            counts[b] = counts.get(b, 0) + 1
    shared = {b for b, n in counts.items() if n * 2 > len(lists)}
    own = kits.get(kit_id, {}).get("blocks")
    return frozenset(b for b in own if b not in shared) if isinstance(own, list) else frozenset()
