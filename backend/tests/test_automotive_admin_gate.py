"""The automotive-core admin gate must fail closed.

``_require_admin`` guards build/activate/rollback of the foundation pack. It
previously returned None (authorized everyone). It now requires a matching
``X-Admin-Key`` and rejects when the key is unconfigured, missing, or wrong.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
_MODULE = (
    BACKEND
    / "app" / "platform_generator" / "overlays" / "automotive_core"
    / "app" / "routers" / "admin_automotive.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("admin_automotive_gate_test", _MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unconfigured_server_rejects(monkeypatch):
    monkeypatch.delenv("AUTOMOTIVE_ADMIN_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_ADMIN_KEY", raising=False)
    mod = _load()
    with pytest.raises(HTTPException) as exc:
        mod._require_admin(x_admin_key="anything")
    assert exc.value.status_code == 503


def test_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("CEREBRUM_ADMIN_KEY", "s3cret-admin-key")
    mod = _load()
    with pytest.raises(HTTPException) as exc:
        mod._require_admin(x_admin_key=None)
    assert exc.value.status_code == 403


def test_wrong_key_rejected(monkeypatch):
    monkeypatch.setenv("CEREBRUM_ADMIN_KEY", "s3cret-admin-key")
    mod = _load()
    with pytest.raises(HTTPException) as exc:
        mod._require_admin(x_admin_key="wrong")
    assert exc.value.status_code == 403


def test_correct_key_allowed(monkeypatch):
    monkeypatch.setenv("CEREBRUM_ADMIN_KEY", "s3cret-admin-key")
    mod = _load()
    # No exception => authorized.
    assert mod._require_admin(x_admin_key="s3cret-admin-key") is None
