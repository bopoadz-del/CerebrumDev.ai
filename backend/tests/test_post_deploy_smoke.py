"""Post-deploy smoke: wait for ready; skip gated checks without a token."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = REPO_ROOT / "scripts" / "post_deploy_smoke.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("post_deploy_smoke", SMOKE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke():
    mod = _load_smoke()
    mod.FAILURES.clear()
    return mod


class TestHasGatedCredentials:
    def test_empty_token_is_unset(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        assert smoke.has_gated_credentials() is False

    def test_whitespace_token_is_unset(self, smoke, monkeypatch):
        monkeypatch.setenv("SMOKE_GATE_TOKEN", "   ")
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        assert smoke.has_gated_credentials() is False

    def test_token_counts(self, smoke, monkeypatch):
        monkeypatch.setenv("SMOKE_GATE_TOKEN", "render-secret")
        assert smoke.has_gated_credentials() is True

    def test_email_pair_counts(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.setenv("SMOKE_EMAIL", "ops@example.com")
        monkeypatch.setenv("SMOKE_PASSWORD", "secret")
        assert smoke.has_gated_credentials() is True


class TestWaitForReady:
    def test_retries_until_ready_then_returns_true(self, smoke):
        calls = {"n": 0}

        def fake_req(method, path, **kwargs):
            calls["n"] += 1
            if calls["n"] < 4:
                return 503, {"status": "not_ready"}
            if path == "/health":
                return 200, {"status": "ok"}
            return 200, {"status": "ready"}

        slept = []
        assert smoke.wait_for_ready(
            timeout_s=30,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=slept.append,
        )
        assert calls["n"] >= 4
        assert smoke.FAILURES == []

    def test_timeout_records_dead_and_returns_false(self, smoke):
        def fake_req(method, path, **kwargs):
            return 502, {"raw": "bounce"}

        ok = smoke.wait_for_ready(
            timeout_s=0,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=lambda _s: None,
        )
        assert ok is False
        assert "health/ready wait" in smoke.FAILURES

    def test_surface_is_ready_requires_ready_status(self, smoke):
        assert smoke.surface_is_ready(200, {"status": "ok"}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, {"status": "ok"}, 503, {"status": "not_ready"})
        assert not smoke.surface_is_ready(502, {}, 502, {})


class TestUnauthenticatedSkip:
    def test_skip_annotation_is_a_notice_not_a_failure(self, smoke, capsys, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        smoke.emit_gated_skip_annotation()
        out = capsys.readouterr().out
        assert "SMOKE SKIP:" in out
        assert "::notice title=Post-deploy smoke::" in out
        assert "gated" in out.lower()
        assert smoke.FAILURES == []

    def test_record_unauthenticated_surface_does_not_require_redis(self, smoke):
        def fake_req(method, path, **kwargs):
            if path == "/health":
                return 200, {"status": "ok", "redis": {"configured": False}}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                return 200, {"git_sha": "abc123456789"}
            raise AssertionError(path)

        smoke.req = fake_req
        smoke.record_unauthenticated_surface(
            health=fake_req("GET", "/health"),
            ready=fake_req("GET", "/ready"),
            version=fake_req("GET", "/version"),
        )
        assert smoke.FAILURES == []
        assert "redis" not in " ".join(smoke.FAILURES)

    def test_finish_passes_when_ungated_and_clean(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        smoke.FAILURES.clear()
        with pytest.raises(SystemExit) as exc:
            smoke.finish()
        assert exc.value.code == 0

    def test_finish_fails_on_timeout_even_without_token(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        smoke.FAILURES.append("health/ready wait")
        with pytest.raises(SystemExit) as exc:
            smoke.finish()
        assert exc.value.code == 1


class TestResolveBaseAndWorkflow:
    def test_import_does_not_treat_pytest_argv_as_host(self, smoke):
        assert smoke.BASE == smoke.DEFAULT_BASE
        assert smoke.resolve_base(["pytest", "tests/test_post_deploy_smoke.py"]) == (
            smoke.DEFAULT_BASE
        )
        assert (
            smoke.resolve_base(["smoke", "https://api.cerebrum-dev.com"])
            == "https://api.cerebrum-dev.com"
        )

    def test_workflow_does_not_hardcode_a_token(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "${{ secrets.SMOKE_GATE_TOKEN }}" in text
        assert "SMOKE_READY_WAIT_S" in text
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("SMOKE_GATE_TOKEN:") and "secrets." not in stripped:
                raise AssertionError(f"hardcoded smoke token line: {line}")

    def test_script_still_retries_transient_and_points_at_live_host(self):
        text = SMOKE_PATH.read_text(encoding="utf-8")
        assert "https://api.cerebrum-dev.com" in text
        assert "cerebrumdev-backend.onrender.com" not in text
        assert "TRANSIENT" in text and "502" in text
        assert "SMOKE_READY_WAIT_S" in text
        assert "has_gated_credentials" in text
