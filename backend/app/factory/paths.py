"""Resolve Factory repo root across local and Docker layouts.

Local checkout::
  <repo>/backend/app/factory/*.py  → root = <repo>

Docker image (Dockerfile copies ``backend/app`` → ``/app/app``)::
  /app/app/factory/*.py            → root = /app
  with ``blueprints/`` copied to ``/app/blueprints``.
"""

from __future__ import annotations

from pathlib import Path


def factory_repo_root(anchor: Path | None = None) -> Path:
    """Return the directory that contains ``blueprints/`` (and factory_outputs)."""
    here = (anchor or Path(__file__)).resolve()
    for root in (here.parents[3], here.parents[2], Path("/app"), Path.cwd()):
        if (root / "blueprints").is_dir():
            return root
    # Prefer local-dev layout when blueprints are absent (tests may monkeypatch)
    return here.parents[3]


class UnsafeOutputDir(ValueError):
    """A requested generation target resolves outside the outputs root."""


def factory_outputs_root() -> Path:
    """The only directory tree generation is allowed to create or destroy."""
    return factory_repo_root() / "factory_outputs"


def is_within_outputs_root(candidate: Path | str) -> bool:
    """Whether ``candidate`` resolves inside :func:`factory_outputs_root`."""
    root = factory_outputs_root().resolve()
    try:
        resolved = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def is_safe_to_clean(candidate: Path | str) -> bool:
    """Whether ``candidate`` may be recursively deleted by the generator.

    Deliberately wider than :func:`is_within_outputs_root`. ``ProductGenerator``
    is a library: the CLI and the test suite legitimately generate into
    temporary directories, and confining it to ``factory_outputs/`` would break
    them. What it must never do is delete somewhere real -- ``/app``,
    ``/app/storage``, a home directory, ``/``.

    The strict check stays where untrusted input actually arrives, on the HTTP
    boundary in :func:`safe_output_dir`. This is the second line, sized so that
    it refuses catastrophe without dictating where trusted callers may work.
    """
    import tempfile

    if is_within_outputs_root(candidate):
        return True
    try:
        resolved = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return False
    if resolved == Path(resolved.anchor):  # a filesystem root
        return False
    tmp_root = Path(tempfile.gettempdir()).resolve()
    return tmp_root in resolved.parents


def safe_output_dir(candidate: Path | str | None, product_id: str) -> Path:
    """Resolve a generation target, refusing anything outside the outputs root.

    ``ProductGenerator.generate`` calls ``shutil.rmtree`` on this path before
    writing, so an unvalidated value is a recursive-delete primitive: a caller
    passing ``/app/storage`` would destroy the accounts database, every session
    and every stored upload. The target is therefore confined to
    ``factory_outputs/``, and traversal is caught by resolving first and then
    checking containment (``..`` segments and symlinks both collapse under
    ``Path.resolve``).
    """
    root = factory_outputs_root()
    if candidate is None or str(candidate).strip() == "":
        return root / product_id
    if not is_within_outputs_root(candidate):
        raise UnsafeOutputDir(
            f"output_dir must resolve inside {root}; refusing {candidate!r}"
        )
    return Path(candidate).resolve()
