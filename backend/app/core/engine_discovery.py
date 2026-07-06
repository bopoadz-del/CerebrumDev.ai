"""Helpers for locating the Cerebrum-Blocks engine checkout on disk."""

import os
from pathlib import Path
from typing import Optional


def _find_engine_root(anchor: Optional[Path] = None) -> Path:
    """Locate the Cerebrum-Blocks engine checkout on disk.

    Checks ``CEREBRUM_BLOCKS_ROOT`` first, then falls back to a sibling
    ``Cerebrum-Blocks`` directory relative to this backend.
    """
    explicit = os.getenv("CEREBRUM_BLOCKS_ROOT")
    if explicit:
        return Path(explicit)
    project_root = (anchor or Path(__file__).resolve()).parents[3]
    for candidate in (
        project_root.parent / "Cerebrum-Blocks",
        project_root.parent.parent / "Cerebrum-Blocks",
    ):
        if candidate.is_dir():
            return candidate
    return project_root.parent / "Cerebrum-Blocks"
