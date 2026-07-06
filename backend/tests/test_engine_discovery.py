"""Tests for packaging-time engine discovery and fetch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.core.engine_discovery import (
    EngineDiscoveryError,
    _fetch_engine_checkout,
    _git_rev_parse,
    resolve_engine_source,
)


@pytest.fixture
def no_local_engine(monkeypatch, tmp_path: Path):
    """Ensure no local engine checkout is discovered and cache is isolated."""
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    # No sibling Cerebrum-Blocks in the temp dir.
    monkeypatch.setattr(
        "app.core.engine_discovery._find_local_engine_root",
        lambda: None,
    )
    # Isolate the fetch cache so tests do not reuse each other's checkouts.
    cache = tmp_path / "engine-cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "app.core.engine_discovery._cache_dir",
        lambda: cache,
    )


def _fake_clone(
    clone_dir: Path,
    ref: str,
    repo: str,
    should_fail: bool = False,
    fail_stderr: str = "",
) -> Any:
    """Build a mock subprocess.run result for git clone."""

    class _Result:
        def __init__(self, returncode: int, stderr: str = ""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    def _run(args: List[str], **kwargs: Dict[str, Any]):
        if args[:2] == ["git", "clone"]:
            if should_fail:
                return _Result(128, fail_stderr)
            # Simulate a shallow clone.
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            git_dir = target / ".git"
            git_dir.mkdir()
            head = target / ".git" / "HEAD"
            head.write_text(f"ref: refs/heads/{ref}\n", encoding="utf-8")
            return _Result(0)
        if args == ["git", "rev-parse", "HEAD"]:
            return _Result(0)
        raise ValueError(f"unexpected git call: {args}")

    return _run


def test_fetch_engine_checkout_caches_by_ref(no_local_engine, tmp_path: Path, monkeypatch):
    """A shallow clone is performed once per ref and cached."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "v1.2.3")
    repo = "https://github.com/example/repo.git"
    calls: List[List[str]] = []

    def _counting_run(args: List[str], **kwargs: Dict[str, Any]):
        calls.append(args)
        return _fake_clone(tmp_path, "v1.2.3", repo)(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)

    path1 = _fetch_engine_checkout(repo, "v1.2.3")
    path2 = _fetch_engine_checkout(repo, "v1.2.3")

    assert path1 == path2
    # Only one clone call; rev-parse may be called twice (once per invocation).
    clone_calls = [c for c in calls if c[:2] == ["git", "clone"]]
    assert len(clone_calls) == 1


def test_resolve_engine_source_records_metadata_for_fetched_engine(
    no_local_engine, tmp_path: Path, monkeypatch
):
    """Fetching the engine records repo, ref, and commit_sha in metadata."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "main")

    def _run(args: List[str], **kwargs: Dict[str, Any]):
        return _fake_clone(tmp_path, "main", "https://github.com/bopoadz-del/Cerebrum-Blocks.git")(
            args, **kwargs
        )

    monkeypatch.setattr(subprocess, "run", _run)

    root, metadata = resolve_engine_source()

    assert metadata["source"] == "fetched"
    assert metadata["repo"] == "https://github.com/bopoadz-del/Cerebrum-Blocks.git"
    assert metadata["ref"] == "main"
    assert "commit_sha" in metadata
    assert root.is_dir()


def test_resolve_engine_source_aborts_when_ref_missing(no_local_engine, monkeypatch):
    """Without a local checkout and without CEREBRUM_BLOCKS_REF, packaging aborts."""
    monkeypatch.delenv("CEREBRUM_BLOCKS_REF", raising=False)

    with pytest.raises(EngineDiscoveryError, match="CEREBRUM_BLOCKS_REF is unset"):
        resolve_engine_source()


def test_fetch_engine_checkout_aborts_on_unreachable_repo(no_local_engine, tmp_path: Path, monkeypatch):
    """A unreachable repo aborts with a clear message naming repo and ref."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "main")
    repo = "https://github.com/unreachable/repo.git"

    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_clone(
            tmp_path,
            "main",
            repo,
            should_fail=True,
            fail_stderr="Could not resolve host: github.com",
        ),
    )

    with pytest.raises(EngineDiscoveryError, match=repo):
        _fetch_engine_checkout(repo, "main")


def test_fetch_engine_checkout_aborts_on_missing_ref(no_local_engine, tmp_path: Path, monkeypatch):
    """A missing ref aborts with a clear message naming repo and ref."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "nonexistent-ref")
    repo = "https://github.com/example/repo.git"

    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_clone(
            tmp_path,
            "nonexistent-ref",
            repo,
            should_fail=True,
            fail_stderr="Remote branch nonexistent-ref not found",
        ),
    )

    with pytest.raises(EngineDiscoveryError, match="nonexistent-ref"):
        _fetch_engine_checkout(repo, "nonexistent-ref")


def test_git_rev_parse_aborts_loudly(tmp_path: Path):
    """_git_rev_parse raises a clear message when the repo is corrupt."""
    bad_repo = tmp_path / "bad-repo"
    bad_repo.mkdir()

    class _BadResult:
        returncode = 128
        stderr = "fatal: not a git repository"
        stdout = ""

    with patch("subprocess.run", return_value=_BadResult()):
        with pytest.raises(EngineDiscoveryError, match="not a git repository"):
            _git_rev_parse(bad_repo)
