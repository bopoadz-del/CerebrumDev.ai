"""Pinned baseline for The_Fork-derived platform generation."""

from __future__ import annotations

# Pinned source commit for the Automotive Safety Intelligence Pilot.
# The generator copies from this commit and applies domain transformations.
# Do not advance this pin without re-running the baseline inspection and
# updating docs/superpowers/records/2026-07-11-fork-baseline-inspection.md.
FORK_BASELINE_COMMIT = "008d5e7a53654d401ce60372212f5245a8067c76"

# Expected top-level directories in the pinned baseline.
FORK_SOURCE_DIRS = {"app", "alembic", "frontend", "Dockerfile", "render.yaml"}
