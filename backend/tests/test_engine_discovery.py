"""Tests for packaging-time engine discovery and fetch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.core.engine_discovery import (
    DEFAULT_CEREBRUM_BLOCKS_REF,
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


def _fake_fetch(
    clone_dir: Path,
    ref: str,
    repo: str,
    should_fail: bool = False,
    fail_stderr: str = "",
) -> Any:
    """Build a mock subprocess.run result for git init/fetch/checkout."""

    class _Result:
        def __init__(self, returncode: int, stderr: str = "", stdout: str = ""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = stdout

    def _run(args: List[str], **kwargs: Dict[str, Any]):
        if args[:2] == ["git", "init"]:
            if should_fail and fail_stderr:
                return _Result(128, fail_stderr)
            cwd = Path(kwargs.get("cwd", clone_dir))
            (cwd / ".git").mkdir(parents=True, exist_ok=True)
            return _Result(0)
        if args[:3] == ["git", "remote", "add"]:
            return _Result(0)
        if args[:2] == ["git", "fetch"]:
            if should_fail:
                return _Result(128, fail_stderr)
            cwd = Path(kwargs.get("cwd", clone_dir))
            head = cwd / ".git" / "HEAD"
            head.write_text(f"ref: refs/heads/{ref}\n", encoding="utf-8")
            return _Result(0)
        if args[:2] == ["git", "checkout"]:
            cwd = Path(kwargs.get("cwd", clone_dir))
            marker = cwd / "fetched"
            marker.write_text(ref, encoding="utf-8")
            return _Result(0)
        if args == ["git", "rev-parse", "HEAD"]:
            return _Result(0, "", "deadbeef" * 5)
        raise ValueError(f"unexpected git call: {args}")

    return _run


def test_fetch_engine_checkout_caches_by_ref(no_local_engine, tmp_path: Path, monkeypatch):
    """A shallow fetch is performed once per ref and cached."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "v1.2.3")
    repo = "https://github.com/example/repo.git"
    calls: List[List[str]] = []

    def _counting_run(args: List[str], **kwargs: Dict[str, Any]):
        calls.append(args)
        return _fake_fetch(tmp_path, "v1.2.3", repo)(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)

    path1 = _fetch_engine_checkout(repo, "v1.2.3")
    path2 = _fetch_engine_checkout(repo, "v1.2.3")

    assert path1 == path2
    # Only one fetch call; rev-parse may be called twice (once per invocation).
    fetch_calls = [c for c in calls if c[:2] == ["git", "fetch"]]
    assert len(fetch_calls) == 1


def test_resolve_engine_source_records_metadata_for_fetched_engine(
    no_local_engine, tmp_path: Path, monkeypatch
):
    """Fetching the engine records repo, ref, and commit_sha in metadata."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "main")

    def _run(args: List[str], **kwargs: Dict[str, Any]):
        return _fake_fetch(tmp_path, "main", "https://github.com/bopoadz-del/Cerebrum-Blocks.git")(
            args, **kwargs
        )

    monkeypatch.setattr(subprocess, "run", _run)

    root, metadata = resolve_engine_source()

    assert metadata["source"] == "fetched"
    assert metadata["repo"] == "https://github.com/bopoadz-del/Cerebrum-Blocks.git"
    assert metadata["ref"] == "main"
    assert "commit_sha" in metadata
    assert root.is_dir()


def test_resolve_engine_source_uses_default_ref_when_unset(
    no_local_engine, tmp_path: Path, monkeypatch
):
    """Without a local checkout and without CEREBRUM_BLOCKS_REF, the pinned default is used."""
    monkeypatch.delenv("CEREBRUM_BLOCKS_REF", raising=False)

    def _run(args: List[str], **kwargs: Dict[str, Any]):
        return _fake_fetch(
            tmp_path, "bb4bf69563fb059cff2da7375379f1e4b767543f", "https://github.com/bopoadz-del/Cerebrum-Blocks.git"
        )(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)

    from app.core.engine_discovery import DEFAULT_CEREBRUM_BLOCKS_REF

    root, metadata = resolve_engine_source()

    assert metadata["source"] == "fetched"
    assert metadata["repo"] == "https://github.com/bopoadz-del/Cerebrum-Blocks.git"
    assert metadata["ref"] == DEFAULT_CEREBRUM_BLOCKS_REF
    assert metadata["ref"] == "bb4bf69563fb059cff2da7375379f1e4b767543f"
    assert "commit_sha" in metadata
    assert root.is_dir()


def test_default_ref_is_pinned_commit(no_local_engine):
    """The default engine ref is the known-good pinned full commit SHA."""
    assert DEFAULT_CEREBRUM_BLOCKS_REF == "bb4bf69563fb059cff2da7375379f1e4b767543f"


def test_fetch_engine_checkout_aborts_on_unreachable_repo(no_local_engine, tmp_path: Path, monkeypatch):
    """A unreachable repo aborts with a clear message naming repo and ref."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_REF", "main")
    repo = "https://github.com/unreachable/repo.git"

    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_fetch(
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
        _fake_fetch(
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
