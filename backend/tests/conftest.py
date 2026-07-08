"""Shared pytest fixtures for CerebrumDev.ai backend tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Shared FastAPI test client."""
    return TestClient(app)
