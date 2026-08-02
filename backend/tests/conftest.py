"""Shared pytest fixtures for CerebrumDev.ai backend tests."""

from __future__ import annotations

import os

# The suite runs without credentials, so it needs the anonymous dev principal.
# That is no longer the default -- it must be asked for -- and app.main runs
# verify_production_auth() at import, so this has to be set BEFORE the import
# below or collection fails.
os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

# TestClient fires startup events; without this every test app would arm the
# nightly backup scheduler and take a bootstrap snapshot into ./storage.
# Scheduler tests opt back in explicitly with monkeypatch.
os.environ.setdefault("BACKUP_SCHEDULE_ENABLED", "0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    """Shared FastAPI test client."""
    return TestClient(app)
