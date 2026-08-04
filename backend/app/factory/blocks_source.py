"""Resolve the Cerebrum-Blocks inventory the generator vendors block code from.

One resolver for every generation door. The chat flow gained the clone
fallback first ("the fix for hollow products"); the HTTP plan/generate routes
kept reading only the env vars, so on a deploy with no local checkout they
silently vendored the stub mirror while chat vendored real block code — the
same generator shipping different fidelity depending on which door the user
came through.

Order: (1) explicit local path via CEREBRUM_BLOCKS_ROOT/_PATH; (2) clone the
Store repo (CEREBRUM_BLOCKS_REPO, GITHUB_TOKEN-auth'd) via engine_discovery.
Returns None only when both fail — the generator then falls back to its
vendor-mirror stubs (honestly labeled).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_blocks_root() -> Optional[Path]:
    root = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    if root:
        return Path(root)
    try:
        from app.core import engine_discovery

        checkout = engine_discovery.find_engine_root()
        # Only use it if it actually carries the block registry the generator
        # copies from — otherwise it's not a usable blocks root.
        if checkout and (Path(checkout) / "block_registry").is_dir():
            logger.info("blocks_root: cloned Store checkout at %s", checkout)
            return Path(checkout)
        logger.warning("blocks_root: checkout %s has no block_registry/", checkout)
    except Exception as exc:  # noqa: BLE001 — never break generation on clone failure
        logger.warning(
            "blocks_root: Store clone unavailable (%s); vendor mirror will be used", exc
        )
    return None
