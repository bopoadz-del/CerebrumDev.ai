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
