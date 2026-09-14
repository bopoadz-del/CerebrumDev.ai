"""Shared factory-suite fixtures."""

from __future__ import annotations

import pytest

from tests.factory.coder_stub import apply_stub_coder


@pytest.fixture()
def stub_coder(monkeypatch):
    """Coder enabled with every entry point stubbed — no paid calls, ever.

    A build under this fixture is a simulation of a coding-agent build,
    not a template-only run: the stubbed coder authors the README and the
    build records ``agent_written >= 1``, so the WRITER gate refuses
    nothing and the deterministic emitters still own the rest of the tree.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_HTTP_ONESHOT", "1")
    patches = apply_stub_coder()
    try:
        yield
    finally:
        for patch in patches:
            patch.stop()
