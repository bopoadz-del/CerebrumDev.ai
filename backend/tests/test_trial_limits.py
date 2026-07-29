"""Server-side trial boundary: per-account quotas on generation, chat, export.

A free trial with no cap is a bill and an outage waiting to happen. Limits are
enforced server-side; active subscribers and non-account principals (admin /
local dev) are exempt.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core import accounts_store
from app.core.trial_limits import (
    TrialLimitExceeded,
    consume,
    quota_snapshot,
    require_within_limit,
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    yield


def _trial_account():
    acct = accounts_store.create_account("trial@example.com", "hunter2hunter2")
    return acct["id"] if "id" in acct else acct["account_id"]


class TestConsume:
    def test_counts_up_and_blocks_at_limit(self, monkeypatch):
        monkeypatch.setenv("TRIAL_GENERATION_LIMIT", "2")
        account_id = _trial_account()

        first = consume(account_id, "generation")
        assert first["allowed"] is True
        assert first["remaining"] == 1
        second = consume(account_id, "generation")
        assert second["allowed"] is True
        assert second["remaining"] == 0
        third = consume(account_id, "generation")
        assert third["allowed"] is False

    def test_daily_counter_is_scoped_per_day(self, monkeypatch):
        monkeypatch.setenv("TRIAL_CHAT_MESSAGES_PER_DAY", "1")
        account_id = _trial_account()
        assert consume(account_id, "chat_message")["allowed"] is True
        assert consume(account_id, "chat_message")["allowed"] is False


class TestGuard:
    def test_trial_account_over_limit_is_refused(self, monkeypatch):
        monkeypatch.setenv("TRIAL_GENERATION_LIMIT", "0")
        account_id = _trial_account()
        with pytest.raises(TrialLimitExceeded) as exc:
            require_within_limit(account_id, "generation")
        assert exc.value.status_code == 429
        assert "generation" in str(exc.value.detail)

    def test_active_subscription_is_exempt(self, monkeypatch):
        monkeypatch.setenv("TRIAL_GENERATION_LIMIT", "0")
        account_id = _trial_account()
        accounts_store.set_subscription(account_id, status="active")
        require_within_limit(account_id, "generation")  # must not raise

    def test_non_account_principal_is_exempt(self, monkeypatch):
        monkeypatch.setenv("TRIAL_GENERATION_LIMIT", "0")
        require_within_limit(None, "generation")  # admin / local-dev: no raise
        require_within_limit("no-such-account", "generation")

    def test_snapshot_reports_all_counters(self):
        account_id = _trial_account()
        snap = quota_snapshot(account_id)
        assert {"generation", "chat_message", "export"} <= set(snap.keys())
        for counter in snap.values():
            assert "limit" in counter and "used" in counter and "remaining" in counter


class TestExportGuard:
    def test_export_endpoint_refuses_over_limit(self, monkeypatch):
        monkeypatch.setenv("TRIAL_EXPORT_LIMIT", "0")
        account_id = _trial_account()

        from app.routers.session_product import _enforce_export_quota

        with pytest.raises(HTTPException) as exc:
            _enforce_export_quota(account_id)
        assert exc.value.status_code == 429
