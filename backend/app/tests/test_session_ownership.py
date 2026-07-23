"""Session ownership guard: user credentials only reach their own sessions.

The guard backs every session-scoped router (chat, config, upload, train,
deploy, drive, session_product). Admin/master and local-dev principals pass
through; cross-account user access gets a non-leaking 404.
"""

import asyncio

import pytest
from fastapi import HTTPException

from app.core import session_store
from app.core.auth import Principal
from app.core.session_guard import require_owned_session
from app.models.session import SessionState


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def owned_session():
    state = SessionState(session_id="sess_guard_test", user_id="acct_owner")
    session_store._session_store[state.session_id] = state
    yield state
    session_store._session_store.pop(state.session_id, None)


def test_owner_user_passes(owned_session):
    principal = Principal(kind="user", account_id="acct_owner")
    state = _run(require_owned_session(owned_session.session_id, principal))
    assert state.session_id == owned_session.session_id


def test_cross_account_user_gets_non_leaking_404(owned_session):
    principal = Principal(kind="user", account_id="acct_intruder")
    with pytest.raises(HTTPException) as exc:
        _run(require_owned_session(owned_session.session_id, principal))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Session not found"


def test_missing_session_404():
    principal = Principal(kind="user", account_id="acct_owner")
    with pytest.raises(HTTPException) as exc:
        _run(require_owned_session("sess_does_not_exist", principal))
    assert exc.value.status_code == 404


def test_admin_principal_passes(owned_session):
    principal = Principal(kind="admin")
    state = _run(require_owned_session(owned_session.session_id, principal))
    assert state.session_id == owned_session.session_id


def test_dev_principal_passes(owned_session):
    principal = Principal(kind="dev")
    state = _run(require_owned_session(owned_session.session_id, principal))
    assert state.session_id == owned_session.session_id
