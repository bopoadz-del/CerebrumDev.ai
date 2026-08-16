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
    backup = body["details"]["last_backup"]
    assert backup["ok"] in {True, False}
    if backup["ok"] is False:
        assert backup.get("error")


def test_version_endpoint_does_not_invent_a_sha(client, monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("SOURCE_VERSION", raising=False)
    res = client.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert body["service"] == "cerebrumdev-factory"
    assert body["git_sha"] is None
    assert body["git_sha_short"] is None
    assert "sentry_configured" in body


def test_version_endpoint_reports_render_sha(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef1234567890")
    res = client.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert body["git_sha"] == "abcdef1234567890"
    assert body["git_sha_short"] == "abcdef1"


def test_ready_head_matches_get_status(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ENV", "test")
    get_res = client.get("/ready")
    head_res = client.head("/ready")
    assert head_res.status_code == get_res.status_code
