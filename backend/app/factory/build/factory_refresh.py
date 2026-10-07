"""Re-write the Factory's own files on a re-entered build.

A build resumed from an earlier run -- or attached from its cerebrum-builds
branch -- carries the Factory files of the Factory that FIRST built it. When
the Factory has since been fixed, those old copies keep failing the build:
live, a hotel platform built before the release-gate fix kept a
``scripts/release_gate.py`` that demands a withheld file, so every resume
failed its Docker image at 0/13 no matter how green the product was.

So on re-entry, before TESTER, the files the Factory owns outright are
re-rendered from the CURRENT templates. Only files that are pure Factory
templates with no coder content; the coder's files (handlers, models, routes,
dispatch) are never touched.

requirements.txt is MERGED, never replaced: every line the build declares
(a Postgres driver, say) stays, and only the vendored blocks' missing
obligations are added.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List

_LF = "\n"


def _dist(line: str):
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    return re.split(r"[<>=!~\[; ]", text, 1)[0].strip().lower().replace("_", "-")


def _write_if_changed(
    root: Path, rel: str, text: str, changed: List[str], *, shared: bool = False
) -> None:
    """Write ``text`` when it differs. A Factory-owned file is normalised to
    LF; a SHARED file (``shared=True``) is written exactly as given, so the
    product's own bytes -- line endings included -- are never rewritten."""
    path = root / rel
    if not shared:
        text = text.replace("\r\n", _LF)
    old = path.read_bytes().decode("utf-8") if path.is_file() else None
    if old is not None and not shared:
        old = old.replace("\r\n", _LF)
    if old == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    changed.append(rel)


def merged_requirements(root: Path) -> str:
    """The build's requirements.txt plus every package the tree needs and lacks.

    Two sources of missing packages: the vendored blocks' own imports, and the
    framework features the coder used whose backing distribution FastAPI does
    not declare (form parsing, templates, session cookies). Both are read off
    the tree, so a branch built before the Factory learned about either gets
    them on the resume that re-enters it.
    """
    from app.factory.build.block_obligations import dependency_obligations_on_disk
    from app.factory.build.factory_block import apply_block, outside
    from app.factory.build.roles_handlers import _render_requirements

    # requirements.txt is SHARED: the product's own lines stay byte-for-byte
    # (factory_block); the Factory's additions live only in its marked block,
    # recomputed from scratch each pass so the edit is idempotent.
    path = root / "requirements.txt"
    existing = path.read_bytes().decode("utf-8") if path.is_file() else ""
    product = outside(existing).replace("\r\n", _LF)
    have = {d for d in (_dist(line) for line in product.split(_LF)) if d}
    extra = []
    for line in _render_requirements(dependency_obligations_on_disk(root), root=root).split(_LF):
        dist = _dist(line)
        if dist and dist not in have:
            extra.append(line)
            have.add(dist)
    if not extra and outside(existing) == existing:
        return existing
    body = (
        "# Packages this tree needs and did not declare: vendored block\n"
        "# imports, and framework features FastAPI does not declare.\n"
        + _LF.join(extra) + (_LF if extra else "")
    )
    return apply_block(existing, body)


def refresh_factory_files(
    root: Path, product_name: str, blueprint: Any = None
) -> List[str]:
    """Re-render Factory-owned files in ``root``; return the ones that changed.

    ``blueprint`` keeps a re-entered build's acceptance harness following the
    brief it declared — re-rendering without it would raise every signal and
    silently re-strict a build whose brief asked for less."""
    from app.factory.build.roles_handlers import _render_release_gate
    from app.factory.build.store_acceptance import render_acceptance_script, render_github_ci

    root = Path(root)
    changed: List[str] = []
    if (root / "scripts" / "release_gate.py").is_file():
        _write_if_changed(root, "scripts/release_gate.py", _render_release_gate(product_name), changed)
    if (root / "scripts" / "acceptance.py").is_file():
        _write_if_changed(
            root,
            "scripts/acceptance.py",
            render_acceptance_script(blueprint),
            changed,
        )
    from app.factory.build.writer_behaviour import SELF_CHECK_REL, render_self_check

    if (root / SELF_CHECK_REL).is_file():
        _write_if_changed(root, SELF_CHECK_REL, render_self_check(), changed)
    if (root / ".github" / "workflows" / "ci.yml").is_file():
        _write_if_changed(root, ".github/workflows/ci.yml", render_github_ci(), changed)
    if (root / "requirements.txt").is_file():
        _write_if_changed(root, "requirements.txt", merged_requirements(root), changed, shared=True)
        from app.factory.build.dependency_pins import CONSTRAINTS_REL, constraints_for_tree

        # requirements.txt's companion, Factory-owned outright and read against
        # the lines just merged: a build first made before the pins existed
        # gets them on re-entry; a tree with no requirements gets nothing.
        _write_if_changed(root, CONSTRAINTS_REL, constraints_for_tree(root), changed)
    return changed
