"""Factory-suite fixtures.

``stub_coder`` exists because of Phase 0.5: a WRITER pass with zero
agent-authored artifacts refuses with ``writer_no_output``, so any test that
needs a green WRITER phase must give the writer a real (stubbed) coding agent.
The stubs are deterministic, so byte-equality tests stay meaningful, and they
never reach a live API. ``stub_coder_patches`` is the same stub for
module-scoped build fixtures, which cannot take a function-scoped
monkeypatch fixture.
"""

from __future__ import annotations

import contextlib
import os
from unittest import mock

import pytest

from tests.factory.coder_stub_bodies import invoking_handler_body


def _stable_oneshot(**kwargs):
    caps = list(kwargs.get("capabilities") or [])
    body = invoking_handler_body({"run": "stable"})
    return {
        "specs": {
            cid: {
                "entity": cid.replace("-", "_"),
                "fields": [{"name": "reference", "type": "str", "required": True}],
            }
            for cid in caps
        },
        "handlers": {cid: body for cid in caps},
        "model": "stub-coder",
    }


def _stable_handler(**kwargs):
    return {"body": invoking_handler_body({"run": "stable"}), "model": "stub-coder"}


def _stable_spec(**kw):
    return {
        "entity": kw["capability_id"].replace("-", "_"),
        "fields": [{"name": "reference", "type": "str", "required": True}],
        "model": "stub-spec",
    }


def _stable_route(**kw):
    return {
        "body": '    return {"ok": True, "capability": CAPABILITY_ID}',
        "model": "stub-route",
    }


_PATCH_TARGETS = {
    "app.factory.build.coder_session.cli_available": lambda command=None: False,
    "app.factory.coder.generate_from_compiled_brief": _stable_oneshot,
    "app.factory.coder.generate_platform_handler": _stable_handler,
    "app.factory.coder.generate_model_spec": _stable_spec,
    "app.factory.coder.generate_route_body": _stable_route,
    "app.factory.coder._llm_code_call": lambda messages: (
        "# Generated platform\n\n## Run it\n\n"
        "pip install -r requirements.txt\n"
        "pip install -r requirements-dev.txt\n"
        "pytest\n",
        "stub-model",
    ),
}

_ENV_OVERRIDES = {
    "FACTORY_CODER_ENABLED": "1",
    "FACTORY_BRIEF_HTTP_ONESHOT": "1",
}


@contextlib.contextmanager
def stub_coder_patches():
    """Apply the deterministic coder stubs for the duration of the block.

    For module-scoped fixtures that build a platform once per module.
    """
    patchers = [mock.patch(name, new=fn) for name, fn in _PATCH_TARGETS.items()]
    old_env = {key: os.environ.get(key) for key in _ENV_OVERRIDES}
    os.environ.update(_ENV_OVERRIDES)
    for patcher in patchers:
        patcher.start()
    try:
        yield
    finally:
        for patcher in patchers:
            patcher.stop()
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture()
def stub_coder(monkeypatch):
    """Enable a fully stubbed, deterministic coding agent.

    Same entry-point set as test_coder_nondeterminism's fixture, but stable
    output. ``FACTORY_BRIEF_HTTP_ONESHOT=1`` pins the compiled-brief path;
    the per-capability entry points are stubbed anyway so no code path can
    reach a live model.
    """
    for key, value in _ENV_OVERRIDES.items():
        monkeypatch.setenv(key, value)
    for name, fn in _PATCH_TARGETS.items():
        monkeypatch.setattr(name, fn)
