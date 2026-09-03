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
def smoke(monkeypatch):
    # CI sets GITHUB_SHA to the PR commit. Tests that do not opt into the
    # SHA gate must see a local-run env so wait_for_ready stays health/ready.
    monkeypatch.delenv("GITHUB_SHA", raising=False)
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

    def test_surface_is_ready_requires_health_ok_and_ready_status(self, smoke):
        assert smoke.surface_is_ready(200, {"status": "ok"}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, {"status": "ok"}, 503, {"status": "not_ready"})
        assert not smoke.surface_is_ready(502, {}, 502, {})
        # HTTP 200 with a missing/degraded health body is a bounce, not ready.
        assert not smoke.surface_is_ready(200, {}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, {"status": "degraded"}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, None, 200, {"status": "ready"})

    def test_bounce_then_matching_sha_succeeds(self, smoke, monkeypatch):
        want = "55f5c8d3b117211fe2e436e8ec76a13483ae79f7"
        monkeypatch.setenv("GITHUB_SHA", want)
        rounds = {"n": 0}

        def fake_req(method, path, **kwargs):
            # Round 1: mid-bounce — health HTTP 200 but status missing, old SHA.
            # Round 2+: health ok, ready, matching SHA (prefix or full).
            if path == "/health":
                rounds["n"] += 1
                if rounds["n"] == 1:
                    return 200, {}
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                if rounds["n"] == 1:
                    return 200, {"git_sha": "725425e636c30774fc13fb82f7318313dca12a69"}
                return 200, {"git_sha": want}
            raise AssertionError(path)

        slept = []
        assert smoke.wait_for_ready(
            timeout_s=30,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=slept.append,
        )
        assert rounds["n"] >= 2
        assert smoke.FAILURES == []

    def test_timeout_when_sha_never_matches_is_dead(self, smoke, monkeypatch):
        monkeypatch.setenv("GITHUB_SHA", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

        def fake_req(method, path, **kwargs):
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                return 200, {"git_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
            raise AssertionError(path)

        ok = smoke.wait_for_ready(
            timeout_s=0,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=lambda _s: None,
        )
        assert ok is False
        assert "health/ready wait" in smoke.FAILURES

    def test_github_sha_unset_does_not_sha_gate(self, smoke, monkeypatch):
        monkeypatch.delenv("GITHUB_SHA", raising=False)

        def fake_req(method, path, **kwargs):
            assert path != "/version", "must not probe /version when GITHUB_SHA unset"
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            raise AssertionError(path)

        assert smoke.wait_for_ready(
            timeout_s=5,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=lambda _s: None,
        )
        assert smoke.FAILURES == []
        # Unique-prefix helper: full vs short, and refuse non-unique shorts.
        full = "55f5c8d3b117211fe2e436e8ec76a13483ae79f7"
        assert smoke.git_sha_matches(full, full)
        assert smoke.git_sha_matches(full, "55f5c8d")
        assert smoke.git_sha_matches("55f5c8d", full)
        assert not smoke.git_sha_matches(full, "55f5c8e")
        assert not smoke.git_sha_matches(full, "55f")
        assert smoke.git_sha_matches("anything", "")


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

    def test_main_bounce_then_sha_then_unauth_skip_passes(self, smoke, monkeypatch, capsys):
        """CI path: wait out bounce + matching SHA, then skip-pass without a token."""
        import sys

        want = "55f5c8d3b117211fe2e436e8ec76a13483ae79f7"
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        monkeypatch.setenv("GITHUB_SHA", want)
        monkeypatch.setenv("SMOKE_READY_WAIT_S", "30")
        monkeypatch.setenv("SMOKE_READY_INTERVAL_S", "0.01")
        monkeypatch.setattr(sys, "argv", ["post_deploy_smoke.py", "https://api.cerebrum-dev.com"])

        rounds = {"n": 0}

        def fake_req(method, path, **kwargs):
            if path == "/health":
                rounds["n"] += 1
                if rounds["n"] == 1:
                    return 200, {}  # status=None — the #283 job failure mode
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                if rounds["n"] == 1:
                    return 200, {"git_sha": "725425e636c30774fc13fb82f7318313dca12a69"}
                return 200, {"git_sha": want}
            raise AssertionError(path)

        smoke.req = fake_req
        with pytest.raises(SystemExit) as exc:
            smoke.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "SMOKE SKIP:" in out
        assert "SMOKE PASS: unauthenticated surface only" in out
        assert "SMOKE FAIL" not in out
        assert rounds["n"] >= 2
        assert smoke.FAILURES == []

    def test_main_unset_sha_does_not_require_version_match(self, smoke, monkeypatch, capsys):
        import sys

        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        monkeypatch.setenv("SMOKE_READY_WAIT_S", "5")
        monkeypatch.setenv("SMOKE_READY_INTERVAL_S", "0.01")
        monkeypatch.setattr(sys, "argv", ["post_deploy_smoke.py", "https://api.cerebrum-dev.com"])

        def fake_req(method, path, **kwargs):
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                return 200, {"git_sha": "not-the-commit-under-test"}
            raise AssertionError(path)

        smoke.req = fake_req
        with pytest.raises(SystemExit) as exc:
            smoke.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "SMOKE SKIP:" in out
        assert "SMOKE PASS: unauthenticated surface only" in out


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
        assert "GITHUB_SHA" in text
        assert "git_sha_matches" in text
