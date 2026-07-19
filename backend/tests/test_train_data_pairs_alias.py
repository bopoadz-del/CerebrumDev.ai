"""train/data accepts pairs alias as well as training_data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.session_store import create_session
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setattr("app.core.auth._API_KEY", "")
    return TestClient(app)


def test_train_data_accepts_pairs_alias(client):
    create_session("sess_train_alias", "tester")
    r = client.post(
        "/v1/sessions/sess_train_alias/train/data",
        json={
            "pairs": [{"question": "Q1", "answer": "A1"}],
            "training_enabled": True,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["training_data_count"] == 1
