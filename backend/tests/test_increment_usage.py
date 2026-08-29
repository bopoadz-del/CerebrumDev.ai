"""M8: increment_usage is an UPSERT and survives a concurrent first insert."""

from __future__ import annotations

import threading

from sqlalchemy.exc import IntegrityError

from app.core import accounts_store


def test_increment_usage_upsert_from_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    account = accounts_store.create_account("upsert@example.com", "hunter2hunter2")
    aid = account["account_id"]
    assert accounts_store.increment_usage(aid, "generation", "lifetime") == 1
    assert accounts_store.increment_usage(aid, "generation", "lifetime") == 2
    assert accounts_store.get_usage(aid, "generation", "lifetime") == 2


def test_increment_usage_retries_integrity_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    account = accounts_store.create_account("race@example.com", "hunter2hunter2")
    aid = account["account_id"]
    assert accounts_store.increment_usage(aid, "generation", "lifetime") == 1

    engine = accounts_store._engine()
    real_begin = engine.begin
    attempts = {"n": 0}

    def _counting_begin():
        ctx = real_begin()
        orig_enter = ctx.__enter__

        def _enter():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise IntegrityError("simulated", params=None, orig=None)
            return orig_enter()

        ctx.__enter__ = _enter  # type: ignore[method-assign]
        return ctx

    monkeypatch.setattr(engine, "begin", _counting_begin)
    value = accounts_store.increment_usage(aid, "generation", "lifetime")
    assert value == 2
    assert attempts["n"] >= 2


def test_increment_usage_concurrent_first_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    account = accounts_store.create_account("threads@example.com", "hunter2hunter2")
    aid = account["account_id"]
    errors: list[BaseException] = []

    def _one():
        try:
            accounts_store.increment_usage(aid, "generation", "lifetime")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert accounts_store.get_usage(aid, "generation", "lifetime") == 8


def test_decrement_usage_refunds(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    account = accounts_store.create_account("refund@example.com", "hunter2hunter2")
    aid = account["account_id"]
    accounts_store.increment_usage(aid, "generation", "lifetime")
    assert accounts_store.decrement_usage(aid, "generation", "lifetime") == 0
    assert accounts_store.decrement_usage(aid, "generation", "lifetime") == 0
