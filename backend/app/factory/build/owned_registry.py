"""Which product files the Factory wrote -- recorded at write time, never listed.

Owner spec (cycle 9): Factory-owned files are DERIVED. Every Factory renderer
that writes a whole file into a product calls :func:`register` (or writes
through :func:`write_owned`) when it writes; the owned set is that registry
united with the stamp registry's OWNED targets and the Store gate's own files
(factory_owned.factory_owned_paths). No module keeps a hand list of names:
a new Factory file is owned the moment the Factory writes it.

The registry lives in two places: in-process (so a pass's snapshot sees what
the Factory just wrote) and in the product tree as :data:`REGISTRY_REL` (so a
later pass, a resume and the status reader see it). The record file is itself
registered -- a writer that edits it is caught like any other owned file -- and
Factory-internal (builds_push.FACTORY_INTERNAL_PATHS: never shipped).
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Set, Tuple

REGISTRY_REL = "docs/factory_owned_registry.json"

_LIVE: Dict[str, Set[str]] = {}


def _norm(rel: Any) -> str:
    text = str(rel or "").replace("\\", "/").strip().strip("/")
    return str(PurePosixPath(text)) if text else ""


def _root_of(target: Any) -> Path:
    """A path, or a workspace handle (its staging/working root)."""
    return Path(getattr(target, "workspace", target))


def _key(root: Path) -> str:
    try:
        return str(root.resolve())
    except OSError:
        return str(root)


def _read(root: Path) -> Set[str]:
    try:
        data = json.loads((root / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    rows = data.get("owned") if isinstance(data, dict) else None
    return {n for n in (_norm(r) for r in rows or ()) if n}


def register(target: Any, *rels: Any) -> None:
    """Record that the Factory wrote ``rels`` into the tree at ``target``.

    ``target`` is a product root or a workspace handle. The record is the
    Factory's internal file, outside every role's lane, so it is written
    directly; through a handle it is added to the handle's ``written`` list,
    so a staged pass commits it with everything else."""
    names = {n for n in (_norm(r) for r in rels) if n}
    if not names:
        return
    root = _root_of(target)
    live = _LIVE.setdefault(_key(root), set())
    live.update(names)
    live.add(REGISTRY_REL)
    owned = sorted(_read(root) | live)
    text = json.dumps({"owned": owned}, indent=2) + "\n"
    path = root / REGISTRY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written = getattr(target, "written", None)
    if isinstance(written, list) and REGISTRY_REL not in written:
        written.append(REGISTRY_REL)


def write_owned(target: Any, rel: Any, text: str) -> bool:
    """Write one Factory file whole and register it; True when bytes changed."""
    root = _root_of(target)
    name = _norm(rel)
    path = root / name
    try:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        current = None
    changed = current != text
    if changed:
        writer = getattr(target, "write_text", None)
        if callable(writer) and not isinstance(target, (str, Path)):
            writer(Path(name), text)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    register(target, name)
    return changed


def registered(*roots: Any) -> Tuple[str, ...]:
    """Every path the Factory registered under any of ``roots``, sorted."""
    out: Set[str] = set()
    for target in roots:
        if target is None:
            continue
        root = _root_of(target)
        out |= _LIVE.get(_key(root), set())
        out |= _read(root)
    return tuple(sorted(out))


def is_registered(rel: Any, *roots: Any) -> bool:
    return _norm(rel) in set(registered(*roots))


def forget(*roots: Iterable[Any]) -> None:
    """Drop the in-process record for ``roots`` (tests)."""
    for target in roots:
        _LIVE.pop(_key(_root_of(target)), None)
