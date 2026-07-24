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

# Pinned, known-good engine ref used when CEREBRUM_BLOCKS_REF is unset.
DEFAULT_CEREBRUM_BLOCKS_REF = "c5a5ba187190242249bf6fe8d915b03bae44cf08"


class EngineDiscoveryError(Exception):
    """Raised when the engine checkout cannot be discovered or fetched."""


def _effective_ref() -> str:
    """Return the engine ref to use when fetching.

    Prefers ``CEREBRUM_BLOCKS_REF``; falls back to the pinned default so
    packaging does not fail simply because the env var is unset.
    """
    return os.getenv("CEREBRUM_BLOCKS_REF") or DEFAULT_CEREBRUM_BLOCKS_REF


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


def _authenticated_repo_url(repo: str) -> str:
    """Return *repo* with GITHUB_TOKEN injected for private HTTPS GitHub URLs.

    The token is never logged. When no token is available or the URL is not a
    plain GitHub HTTPS URL, *repo* is returned unchanged.
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return repo
    if not repo.startswith("https://github.com/"):
        return repo
    # https://github.com/owner/repo.git -> https://<token>@github.com/owner/repo.git
    return repo.replace("https://github.com/", f"https://{token}@github.com/", 1)


def _sanitize_stderr(stderr: str) -> str:
    """Strip any GITHUB_TOKEN from git command stderr before raising/logging."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token or not stderr:
        return stderr or ""
    return stderr.replace(token, "<redacted>")


def _fetch_engine_checkout(repo: str, ref: str) -> Path:
    """Clone or refresh a shallow engine checkout at *ref* and return its path.

    The checkout is cached under ``<tmp>/cerebrumdev/engine-cache/<ref>``.
    Existing cache entries are reused without re-cloning.

    Supports branch/tag names and commit SHAs (short or full). When
    ``GITHUB_TOKEN`` is set and *repo* is a GitHub HTTPS URL, the token is used
    to authenticate the clone so private repositories can be fetched.
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

    authenticated_repo = _authenticated_repo_url(repo)
    logger.info("Fetching engine at ref %s into %s", ref, cache)
    cache.mkdir(parents=True, exist_ok=True)

    def _git(cmd: list[str], cwd: Path = cache) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return result

    init = _git(["git", "init"])
    if init.returncode != 0:
        raise EngineDiscoveryError(
            f"Failed to initialise engine cache for {repo} at ref {ref}: {_sanitize_stderr(init.stderr)}"
        )

    remote_add = _git(["git", "remote", "add", "origin", authenticated_repo])
    if remote_add.returncode != 0:
        raise EngineDiscoveryError(
            f"Failed to add remote for engine {repo} at ref {ref}: {_sanitize_stderr(remote_add.stderr)}"
        )

    fetch = _git(["git", "fetch", "--depth", "1", "origin", ref])
    if fetch.returncode != 0:
        raise EngineDiscoveryError(
            f"Failed to fetch engine {repo} at ref {ref}: {_sanitize_stderr(fetch.stderr)}"
        )

    checkout = _git(["git", "checkout", "FETCH_HEAD"])
    if checkout.returncode != 0:
        raise EngineDiscoveryError(
            f"Failed to checkout engine {repo} at ref {ref}: {_sanitize_stderr(checkout.stderr)}"
        )

    commit_sha = _git_rev_parse(cache)
    logger.info("Fetched engine %s at ref %s (%s)", repo, ref, commit_sha)
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
    none is found, fetch ``CEREBRUM_BLOCKS_REPO`` at the effective ref
    (``CEREBRUM_BLOCKS_REF`` or ``DEFAULT_CEREBRUM_BLOCKS_REF``) into a temp
    cache.

    Raises:
        EngineDiscoveryError: if no local checkout exists and fetching fails.
    """
    local = _find_local_engine_root(anchor)
    if local is not None:
        logger.debug("Using local engine checkout at %s", local)
        return local

    ref = _effective_ref()
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
            "repo": CEREBRUM_BLOCKS_REPO,
            "ref": os.getenv("CEREBRUM_BLOCKS_REF")
            or (commit_sha if commit_sha != "unknown" else DEFAULT_CEREBRUM_BLOCKS_REF),
            "path": str(local),
            "commit_sha": commit_sha,
        }

    ref = _effective_ref()
    fetched = _fetch_engine_checkout(CEREBRUM_BLOCKS_REPO, ref)
    commit_sha = _git_rev_parse(fetched)
    return fetched, {
        "source": "fetched",
        "repo": CEREBRUM_BLOCKS_REPO,
        "ref": ref,
        "commit_sha": commit_sha,
    }
