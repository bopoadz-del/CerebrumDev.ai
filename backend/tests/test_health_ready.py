"""Factory health/ready endpoint tests."""

from __future__ import annotations


def test_health_reports_storage(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ok", "degraded"}
    assert "storage" in body


def test_ready_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    res = client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ready", "not_ready"}
    assert "checks" in body
