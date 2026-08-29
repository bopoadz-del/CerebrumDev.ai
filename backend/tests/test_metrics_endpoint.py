"""Prometheus /metrics is an ops surface — master key, not world-open."""

from __future__ import annotations


def test_metrics_unauth_is_401_when_master_key_is_configured(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.get("/metrics")
    assert res.status_code == 401


def test_metrics_wrong_key_is_401(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_metrics_authorized_is_200_prometheus_text(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.get("/metrics", headers={"Authorization": "Bearer master-secret"})
    assert res.status_code == 200
    body = res.text
    assert "cerebrumdev_up" in body
    assert "process_cpu_seconds_total" in body or "python_info" in body
    content_type = res.headers.get("content-type", "")
    assert content_type.startswith("text/plain")


def test_metrics_absent_master_key_is_404_not_world_open(client, monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    res = client.get("/metrics")
    assert res.status_code == 404
