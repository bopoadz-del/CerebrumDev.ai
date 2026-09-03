"""Coder LLM calls are bounded: timeout, one alternate retry, then fail closed.

Live sess_3f3115ba6d764102 sat in WRITER with "quiet for 14+ min — model
call may still be running" because httpx's idle read timeout never fired
on a streaming/keepalive completion. These tests pin the wall-clock
watchdog, the alternate-model retry, the terminal message, and the Floor
status that must not claim progress while the call is in flight.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
import httpx
import pytest

from app.factory import coder
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import RoleContext, RoleError
from app.factory.build_jobs import build_status


def _kimi_cfg(**overrides):
    cfg = {
        "provider": "kimi",
        "model": "primary",
        "fallback_model": "fallback",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key": "test-key-not-real",
    }
    cfg.update(overrides)
    return cfg


def _arm_kimi(monkeypatch, **overrides):
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: _kimi_cfg(**overrides),
    )
    monkeypatch.setattr(
        "app.core.llm_config.get_factory_fallback_leg", lambda: None
    )
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_watchdog_fires_when_httpx_never_returns(monkeypatch):
    """(a) A hung post is cut off by the wall-clock join, not by idle-read."""
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "0.15")
    _arm_kimi(monkeypatch, fallback_model=None)

    def _hang(url, json=None, headers=None, timeout=None):
        time.sleep(8)
        raise AssertionError("watchdog should have released the caller")

    monkeypatch.setattr(coder.httpx, "post", _hang)

    started = time.monotonic()
    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, elapsed
    assert "coder LLM timed out" in str(exc.value)


def test_timeout_retries_the_alternate_configured_model(monkeypatch):
    """(b) First-leg timeout tries the fallback model; it may still succeed."""
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "30")
    _arm_kimi(monkeypatch)
    models = []

    def _post(url, json=None, headers=None, timeout=None):
        model = (json or {}).get("model")
        models.append(model)
        if model == "primary":
            raise httpx.ReadTimeout("model did not answer")
        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {"content": "return {}"},
                            "finish_reason": "stop",
                        }
                    ]
                }

        return _R()

    monkeypatch.setattr(coder.httpx, "post", _post)

    text, model_used = coder._llm_code_call([{"role": "user", "content": "u"}])
    assert text == "return {}"
    assert model_used == "fallback"
    assert models == ["primary", "fallback"]


def test_timeout_exhaustion_raises_coder_timeout_not_a_generic_failure(
    monkeypatch,
):
    """(c) After every configured leg times out, the error names the timeout."""
    _arm_kimi(monkeypatch)
    models = []

    def _timeout(url, json=None, headers=None, timeout=None):
        models.append((json or {}).get("model"))
        raise httpx.ReadTimeout("model did not answer")

    monkeypatch.setattr(coder.httpx, "post", _timeout)

    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])

    message = str(exc.value)
    assert message.startswith("coder LLM timed out")
    assert "primary" in message
    assert "fallback" in message
    # One attempt per model — do not loop the same slug.
    assert models == ["primary", "fallback"]


def test_timeout_without_alternate_retries_the_same_model_once(monkeypatch):
    _arm_kimi(monkeypatch, fallback_model=None)
    calls = []

    def _timeout(url, json=None, headers=None, timeout=None):
        calls.append(1)
        raise httpx.ReadTimeout("model did not answer")

    monkeypatch.setattr(coder.httpx, "post", _timeout)

    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])

    assert "coder LLM timed out" in str(exc.value)
    assert "after retry" in str(exc.value)
    assert len(calls) == 2


def test_writer_timeout_is_a_role_error_not_a_silent_template(monkeypatch):
    """A hung handler must stop WRITER — not ship a thin scaffold SUCCESS."""
    from app.factory.build import roles_handlers

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")

    def _boom(**kwargs):
        raise coder.CoderTimeout("coder LLM timed out on primary after retry")

    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler", _boom
    )
    monkeypatch.setattr("app.factory.coder.coder_enabled", lambda: True)
    monkeypatch.setattr(
        "app.factory.build.roles_handlers._budget_too_low", lambda ctx, what: False
    )

    notes = []

    def _progress(detail, payload):
        notes.append((detail, payload))

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=type("B", (), {"product_name": "Vet", "vertical": "vet"})(),
        plan=None,
        progress=_progress,
    )
    cap = type(
        "C",
        (),
        {"capability_id": "appointment_scheduling", "notes": "book visits"},
    )()

    with pytest.raises(RoleError) as exc:
        roles_handlers._coder_body(ctx, cap, [], {"fields": []})

    assert "coder LLM timed out writing handler appointment_scheduling" in str(
        exc.value
    )
    assert notes, "must emit a calling-NOTE before the model call"
    detail, payload = notes[0]
    assert detail.startswith("calling coder LLM")
    assert payload.get("model_call") is True
    assert payload.get("stage") == "coder"
    assert "appointment_scheduling" not in detail or "wrote handler" not in detail


def test_calling_note_does_not_claim_handler_progress(tmp_path):
    """(d) In-flight model call keeps done at the last real write."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vet", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler appointment_scheduling (coder LLM (kimi-k2.7-code))",
        payload={"stage": "handlers", "done": 2, "total": 6},
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for clinical_treatment_notes",
        payload={
            "stage": "handlers",
            "capability": "clinical_treatment_notes",
            "model_call": True,
            "deadline_s": 390,
        },
    )

    status = build_status(out)
    assert status["state"] == "building"
    assert status["model_call_in_progress"] is True
    assert status["model_call_deadline_s"] == 390
    assert "calling coder LLM" in status["last_event"]
    assert "clinical_treatment_notes" in status["last_event"]
    # No done/total on the calling NOTE — must not look like handler 3/6.
    assert "phase_progress" not in status
    assert status["activity_done"] is None


def test_overdue_model_call_fails_the_build_status(tmp_path):
    """Past the watchdog wall the Floor must stop, not climb 'quiet for N'."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vet", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for clinical_treatment_notes",
        payload={
            "stage": "handlers",
            "model_call": True,
            "deadline_s": 30,
        },
    )
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    status = build_status(out)
    assert status["state"] == "failed", status
    assert "coder LLM timed out" in status["detail"]
    assert "clinical_treatment_notes" in status["detail"]
    assert status["pilot_ready"] is False


def test_generic_coder_error_still_templates(monkeypatch):
    """Timeout is fatal; a refused/empty completion still ships the template."""
    from app.factory.build import roles_handlers

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr("app.factory.coder.coder_enabled", lambda: True)
    monkeypatch.setattr(
        "app.factory.build.roles_handlers._budget_too_low", lambda ctx, what: False
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler",
        lambda **kw: (_ for _ in ()).throw(coder.CoderError("model refused")),
    )
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=type("B", (), {"product_name": "Vet", "vertical": "vet"})(),
        plan=None,
    )
    cap = type("C", (), {"capability_id": "x", "notes": ""})()
    assert roles_handlers._coder_body(ctx, cap, [], {}) is None
    assert "model refused" in ctx.state["coder_failures"]["x"]
