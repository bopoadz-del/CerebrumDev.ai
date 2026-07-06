"""Helpers for locating the Cerebrum-Blocks engine checkout on disk.

Supports two resolution paths:

1. Local checkout via ``CEREBRUM_BLOCKS_ROOT`` or a sibling ``Cerebrum-Blocks``
   directory.
2. Fetch-on-demand: when no local checkout exists, clone
   ``CEREBRUM_BLOCKS_REPO`` at the pinned ref ``CEREBRUM_BLOCKS_REF`` into a
   temp cache. The cache is keyed by ref so repeated packagings reuse the
   checkout.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CEREBRUM_BLOCKS_REPO = os.getenv(
    "CEREBRUM_BLOCKS_REPO",
    "https://github.com/bopoadz-del/Cerebrum-Blocks.git",
)


class EngineDiscoveryError(Exception):
    """Raised when the engine checkout cannot be discovered or fetched."""


def _cache_dir() -> Path:
    """Return the persistent cache directory for fetched engine checkouts."""
    base = Path(tempfile.gettempdir()) / "cerebrumdev" / "engine-cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _git_rev_parse(repo_dir: Path) -> str:
    """Return the full commit SHA at HEAD of *repo_dir*."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise EngineDiscoveryError(
            f"Could not read commit SHA from engine checkout at {repo_dir}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _fetch_engine_checkout(repo: str, ref: str) -> Path:
    """Clone or refresh a shallow engine checkout at *ref* and return its path.

    The checkout is cached under ``<tmp>/cerebrumdev/engine-cache/<ref>``.
    Existing cache entries are reused without re-cloning.
    """
    cache = _cache_dir() / ref.replace("/", "_")

    if cache.exists() and (cache / ".git").is_dir():
        try:
            commit_sha = _git_rev_parse(cache)
            logger.info("Using cached engine checkout %s at %s (%s)", ref, cache, commit_sha)
            return cache
        except EngineDiscoveryError:
            logger.warning("Cached engine checkout at %s appears corrupt; re-cloning", cache)
            shutil.rmtree(cache, ignore_errors=True)

    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)

    cache.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Cloning engine %s at ref %s into %s", repo, ref, cache)
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, repo, str(cache)],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        raise EngineDiscoveryError(
            f"Failed to clone engine repo {repo} at ref {ref}: {clone.stderr.strip()}"
        )

    if not (cache / ".git").is_dir():
        raise EngineDiscoveryError(
            f"Engine clone succeeded but .git is missing at {cache}; repo={repo}, ref={ref}"
        )

    commit_sha = _git_rev_parse(cache)
    logger.info("Cloned engine %s at ref %s (%s)", repo, ref, commit_sha)
    return cache


def _find_local_engine_root(anchor: Optional[Path] = None) -> Optional[Path]:
    """Locate a local engine checkout, if one exists."""
    explicit = os.getenv("CEREBRUM_BLOCKS_ROOT")
    if explicit:
        path = Path(explicit)
        if path.is_dir():
            return path
        raise EngineDiscoveryError(
            f"CEREBRUM_BLOCKS_ROOT points to a non-existent directory: {path}"
        )

    project_root = (anchor or Path(__file__).resolve()).parents[3]
    for candidate in (
        project_root.parent / "Cerebrum-Blocks",
        project_root.parent.parent / "Cerebrum-Blocks",
    ):
        if candidate.is_dir():
            return candidate

    return None


def find_engine_root(anchor: Optional[Path] = None) -> Path:
    """Locate the Cerebrum-Blocks engine checkout.

    Prefer a local checkout (``CEREBRUM_BLOCKS_ROOT`` or sibling directory). If
    none is found, clone ``CEREBRUM_BLOCKS_REPO`` at the pinned ref
    ``CEREBRUM_BLOCKS_REF`` into a temp cache.

    Raises:
        EngineDiscoveryError: if no local checkout exists and fetching is
            disabled, the ref is unset, or the clone/checkout fails.
    """
    local = _find_local_engine_root(anchor)
    if local is not None:
        logger.debug("Using local engine checkout at %s", local)
        return local

    ref = os.getenv("CEREBRUM_BLOCKS_REF")
    if not ref:
        raise EngineDiscoveryError(
            "No local Cerebrum-Blocks checkout found and CEREBRUM_BLOCKS_REF is unset. "
            "Set CEREBRUM_BLOCKS_ROOT to a local checkout or CEREBRUM_BLOCKS_REF to a "
            "branch/tag to fetch."
        )

    return _fetch_engine_checkout(CEREBRUM_BLOCKS_REPO, ref)


def _find_engine_root(anchor: Optional[Path] = None) -> Path:
    """Backward-compatible alias for :func:`find_engine_root`."""
    return find_engine_root(anchor)


def resolve_engine_source() -> Tuple[Path, dict]:
    """Resolve the engine checkout and return its path plus provenance metadata.

    Returns:
        (engine_root, metadata) where metadata is a dict with ``source``
        (``"local"`` or ``"fetched"``), ``repo``, ``ref``, and ``commit_sha``
        when applicable.
    """
    local = _find_local_engine_root()
    if local is not None:
        try:
            commit_sha = _git_rev_parse(local)
        except EngineDiscoveryError:
            commit_sha = "unknown"
        return local, {
            "source": "local",
            "path": str(local),
            "commit_sha": commit_sha,
        }

    ref = os.getenv("CEREBRUM_BLOCKS_REF")
    if not ref:
        raise EngineDiscoveryError(
            "No local Cerebrum-Blocks checkout found and CEREBRUM_BLOCKS_REF is unset. "
            "Set CEREBRUM_BLOCKS_ROOT to a local checkout or CEREBRUM_BLOCKS_REF to a "
            "branch/tag to fetch."
        )

    fetched = _fetch_engine_checkout(CEREBRUM_BLOCKS_REPO, ref)
    commit_sha = _git_rev_parse(fetched)
    return fetched, {
        "source": "fetched",
        "repo": CEREBRUM_BLOCKS_REPO,
        "ref": ref,
        "commit_sha": commit_sha,
    }
