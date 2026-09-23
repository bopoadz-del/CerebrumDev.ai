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
from typing import List

_LF = "\n"


def _dist(line: str):
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    return re.split(r"[<>=!~\[; ]", text, 1)[0].strip().lower().replace("_", "-")


def _write_if_changed(root: Path, rel: str, text: str, changed: List[str]) -> None:
    path = root / rel
    text = text.replace("\r\n", _LF)
    old = path.read_bytes().decode("utf-8").replace("\r\n", _LF) if path.is_file() else None
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
    from app.factory.build.roles_handlers import _render_requirements

    path = root / "requirements.txt"
    existing = path.read_bytes().decode("utf-8").replace("\r\n", _LF) if path.is_file() else ""
    have = {d for d in (_dist(line) for line in existing.split(_LF)) if d}
    extra = []
    for line in _render_requirements(dependency_obligations_on_disk(root), root=root).split(_LF):
        dist = _dist(line)
        if dist and dist not in have:
            extra.append(line)
            have.add(dist)
    if not extra:
        return existing
    return (
        existing.rstrip(_LF)
        + _LF + _LF
        + "# Packages this tree needs and did not declare: vendored block\n"
        + "# imports, and framework features FastAPI does not declare\n"
        + "# (refreshed by the factory)."
        + _LF + _LF.join(extra) + _LF
    )


def refresh_factory_files(root: Path, product_name: str) -> List[str]:
    """Re-render Factory-owned files in ``root``; return the ones that changed."""
    from app.factory.build.roles_handlers import _render_release_gate
    from app.factory.build.store_acceptance import render_acceptance_script, render_github_ci

    root = Path(root)
    changed: List[str] = []
    if (root / "scripts" / "release_gate.py").is_file():
        _write_if_changed(root, "scripts/release_gate.py", _render_release_gate(product_name), changed)
    if (root / "scripts" / "acceptance.py").is_file():
        _write_if_changed(root, "scripts/acceptance.py", render_acceptance_script(), changed)
    if (root / ".github" / "workflows" / "ci.yml").is_file():
        _write_if_changed(root, ".github/workflows/ci.yml", render_github_ci(), changed)
    if (root / "requirements.txt").is_file():
        _write_if_changed(root, "requirements.txt", merged_requirements(root), changed)
    return changed
