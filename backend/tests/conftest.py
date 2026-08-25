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
os.environ.pop("SMOKE_GATE_TOKEN", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


#: Env vars that arm the coder's cross-provider fallback leg. OPENROUTER_API_KEY
#: is a normal thing to have exported in a dev shell, and it adds a REAL second
#: fallback leg to every coder failure path. Without this fixture, assertions
#: like "exactly two model legs were tried" pass or fail depending on whose
#: machine runs them. Tests that want the leg set these themselves.
_FALLBACK_LEG_ENV = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "FACTORY_LLM_FALLBACK_API_KEY",
    "FACTORY_LLM_FALLBACK_MODEL",
    "FACTORY_LLM_FALLBACK_PROVIDER",
    "FACTORY_LLM_FALLBACK_ALLOW_PAID",
)


@pytest.fixture(autouse=True)
def _no_ambient_fallback_leg(monkeypatch):
    """Keep the cross-provider fallback leg out of tests unless asked for."""
    for var in _FALLBACK_LEG_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client() -> TestClient:
    """Shared FastAPI test client."""
    return TestClient(app)
