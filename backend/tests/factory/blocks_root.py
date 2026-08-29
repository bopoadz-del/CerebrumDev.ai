"""Shared resolver for a real Cerebrum-Blocks checkout in factory tests."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def real_blocks_root():
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    candidates = [Path(env)] if env else []
    candidates.append(ROOT.parent / "Cerebrum-Blocks")
    for candidate in candidates:
        if candidate and (candidate / "block_registry").is_dir():
            return candidate
    return None
