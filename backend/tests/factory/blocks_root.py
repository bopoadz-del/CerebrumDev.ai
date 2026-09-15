"""Resolver for the blocks tests may use: DECLARED inventory only.

``blocks.lock.json`` is the declaration: it pins the store repo at a sha and
lists the blocks, and ``backend/app/factory/vendor_blocks_mirror`` is the
vendored copy of exactly that content (kept honest by
``scripts/verify_vendor_parity.py``, which fails closed when the mirror and
the lock disagree).

A sibling ``Cerebrum-Blocks`` checkout is NOT declared inventory. It floats at
whatever sha the developer happens to have, so letting it win meant the same
test asserted different content on CI (no sibling -> mirror) and on a laptop
(sibling -> some other revision). That is why the sibling-backed tests drifted
red locally while passing in CI.

So a real checkout is honoured only when it IS the declared inventory - its
HEAD must equal ``blocks.lock.json``'s ``store.sha``. A drifted checkout is
ignored rather than silently substituted, and callers fall back to the
vendored mirror (or skip, where they need the registry).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT / "blocks.lock.json"


def declared_store_sha() -> str | None:
    """The store sha pinned in blocks.lock.json, or None if unreadable."""
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sha = ((lock.get("store") or {}).get("sha") or "").strip()
    return sha or None


def _head_sha(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def real_blocks_root():
    """A sibling checkout, but only when it matches the declared store sha.

    Returns None when no candidate exists, when it lacks ``block_registry``,
    or when it has drifted off the declared sha - so tests never assert
    against undeclared content.
    """
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    candidates = [Path(env)] if env else []
    candidates.append(ROOT.parent / "Cerebrum-Blocks")

    declared = declared_store_sha()
    for candidate in candidates:
        if not candidate or not (candidate / "block_registry").is_dir():
            continue
        if declared and _head_sha(candidate) != declared:
            # Present but undeclared: ignore it rather than test against it.
            continue
        return candidate
    return None
