"""The coder's cross-provider fallback leg (OpenRouter / GLM).

The failure this exists for: an out-of-credit or rate-limited Moonshot account
fails BOTH configured Kimi models the same way, so the same-vendor
``fallback_model`` is no fallback at all for the failure that happens most.
The leg only runs after the primary vendor has exhausted its own fallback.

Two properties are load-bearing and both are asserted here:

* the leg carries its OWN endpoint and key -- it must never post the Kimi key
  to OpenRouter, or the OpenRouter key to Moonshot;
* the model that ANSWERED is what gets reported, because that string is
  stamped into every generated module as ``CODER_MODEL``.
"""

from __future__ import annotations

import httpx
import pytest

import app.factory.coder as coder
from app.core.llm_config import (
    DEFAULT_OPENROUTER_FALLBACK_MODEL,
    OPENROUTER_BASE_URL,
    get_factory_fallback_leg,
)

FAKE_KIMI = "sk-kimi-not-real"
FAKE_OPENROUTER = "sk-or-v1-not-real"


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _ok(text: str):
    return _Resp({"choices": [{"message": {"content": text}}]})


# -- arming the leg --------------------------------------------------------


def test_the_leg_is_unarmed_without_a_key():
    """No key, no leg. Absence is None, not a leg that fails at call time."""
    assert get_factory_fallback_leg() is None


def test_a_key_arms_the_leg_on_the_free_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)

    leg = get_factory_fallback_leg()

    assert leg is not None
    assert leg["provider"] == "openrouter"
    assert leg["base_url"] == OPENROUTER_BASE_URL
    assert leg["model"] == DEFAULT_OPENROUTER_FALLBACK_MODEL
    assert leg["model"].endswith(":free"), (
        "the default fallback must be a zero-priced slug; plain z-ai/glm-5.2 "
        "is the paid tier and would start a bill nobody asked for"
    )
    assert "error" not in leg


def test_it_can_be_turned_off_even_with_a_key_present(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_PROVIDER", "none")

    assert get_factory_fallback_leg() is None


def test_a_paid_model_is_refused_unless_asked_for_by_name(monkeypatch):
    """The no-cost-surprise invariant, preserved literally."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_MODEL", "z-ai/glm-5.2")

    leg = get_factory_fallback_leg()

    assert leg is not None
    assert "ALLOW_PAID" in leg["error"]


def test_a_paid_model_is_allowed_when_it_is(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_MODEL", "z-ai/glm-5.2")
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_ALLOW_PAID", "1")

    leg = get_factory_fallback_leg()

    assert leg is not None and "error" not in leg
    assert leg["model"] == "z-ai/glm-5.2"


# -- the leg in the coder --------------------------------------------------


@pytest.fixture
def kimi_env(monkeypatch):
    for var in ("LLM_TEMPERATURE", "CEREBRUM_FACTORY_LLM_MODEL", "CEREBRUM_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)
    monkeypatch.setenv("KIMI_MODEL", "primary-model")
    monkeypatch.setenv("KIMI_FALLBACK_MODEL", "vendor-fallback-model")
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_the_cross_provider_leg_answers_when_both_kimi_models_fail(
    monkeypatch, kimi_env
):
    """The out-of-credit case: both Kimi legs fail identically, GLM answers."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append({"url": url, "model": json["model"], "headers": headers})
        if "openrouter" in url:
            return _ok("return {}")
        raise RuntimeError("insufficient balance")

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    text, model_used = coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert text == "return {}"
    assert model_used == DEFAULT_OPENROUTER_FALLBACK_MODEL, (
        "provenance must name the model that actually answered"
    )
    assert [p["model"] for p in posts] == [
        "primary-model",
        "vendor-fallback-model",
        DEFAULT_OPENROUTER_FALLBACK_MODEL,
    ], "the vendor's own fallback must be tried before crossing providers"


def test_the_leg_carries_its_own_key_not_the_primary_s(monkeypatch, kimi_env):
    """Credentials travel with the leg. Leaking either key to the other host
    would hand a live secret to a third party."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append({"url": url, "auth": headers.get("Authorization")})
        if "openrouter" in url:
            return _ok("return {}")
        raise RuntimeError("down")

    monkeypatch.setattr(coder.httpx, "post", fake_post)
    coder._llm_code_call([{"role": "user", "content": "hi"}])

    moonshot = [p for p in posts if "openrouter" not in p["url"]]
    openrouter = [p for p in posts if "openrouter" in p["url"]]

    assert moonshot and openrouter
    assert all(p["auth"] == f"Bearer {FAKE_KIMI}" for p in moonshot)
    assert all(p["auth"] == f"Bearer {FAKE_OPENROUTER}" for p in openrouter)
    assert FAKE_KIMI not in str(openrouter)


def test_the_leg_is_not_touched_when_the_primary_answers(monkeypatch, kimi_env):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append(url)
        return _ok("return {}")

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    text, model_used = coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert text == "return {}"
    assert model_used == "primary-model"
    assert not any("openrouter" in u for u in posts), (
        "a healthy primary must not send anything to a third party"
    )


def test_a_misconfigured_leg_does_not_run_and_says_why(monkeypatch, kimi_env, caplog):
    """Armed but refused (paid slug) is not the same as absent -- the reason
    has to reach the log, or the leg looks like it silently did not exist."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_MODEL", "z-ai/glm-5.2")
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append(url)
        raise RuntimeError("down")

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    with caplog.at_level("WARNING"):
        with pytest.raises(coder.CoderError):
            coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert not any("openrouter" in u for u in posts)
    assert "not armed" in caplog.text


def test_every_failed_leg_is_named_in_the_error(monkeypatch, kimi_env):
    """coder_failures is often the only record anyone sees."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)

    def fake_post(url, json=None, headers=None, timeout=None):
        raise RuntimeError("everything is down")

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    with pytest.raises(coder.CoderError) as exc:
        coder._llm_code_call([{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert "primary-model" in message
    assert "vendor-fallback-model" in message
    assert DEFAULT_OPENROUTER_FALLBACK_MODEL in message
    assert "everything is down" in message, "the reason, not just the class name"


# -- the free pool's 429 ---------------------------------------------------
#
# Measured live on 2026-08-25: the very first call to z-ai/glm-5.2:free on an
# unused key returned 429 with limit_source="upstream_provider_shared_pool"
# and Retry-After: 5. A leg that gives up on that is a leg that rarely works.


def _throttled(retry_after="5"):
    class _R:
        status_code = 429
        headers = {"Retry-After": retry_after}

    return httpx.HTTPStatusError("429", request=None, response=_R())


def test_a_free_leg_waits_out_a_429_and_succeeds(monkeypatch, kimi_env):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    slept, calls = [], []
    monkeypatch.setattr(coder, "_retry_after_s", lambda exc: 5.0)
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if "openrouter" not in url:
            raise RuntimeError("kimi down")
        if len([c for c in calls if "openrouter" in c]) == 1:
            raise _throttled()
        return _ok("return {}")

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    text, model_used = coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert text == "return {}"
    assert model_used == DEFAULT_OPENROUTER_FALLBACK_MODEL
    assert 5.0 in slept, "the server's Retry-After was ignored"


def test_a_paid_leg_does_not_retry_a_429(monkeypatch, kimi_env):
    """The no-money-on-the-same-answer rule still holds where it costs money."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_MODEL", "z-ai/glm-5.2")
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_ALLOW_PAID", "1")
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if "openrouter" not in url:
            raise RuntimeError("kimi down")
        raise _throttled()

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    with pytest.raises(coder.CoderError):
        coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert len([c for c in calls if "openrouter" in c]) == 1, (
        "a paid 429 was retried — that spends money on the same answer"
    )


def test_a_non_429_status_is_still_not_retried(monkeypatch, kimi_env):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    class _R:
        status_code = 500
        headers: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if "openrouter" not in url:
            raise RuntimeError("kimi down")
        raise httpx.HTTPStatusError("500", request=None, response=_R())

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    with pytest.raises(coder.CoderError):
        coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert len([c for c in calls if "openrouter" in c]) == 1


def test_a_hostile_retry_after_cannot_stall_the_build():
    """A third party must not be able to park a build for an hour."""
    assert coder._retry_after_s(_throttled("3600")) == coder.MAX_RETRY_AFTER_S
    assert coder._retry_after_s(_throttled("nonsense")) == 5.0
    assert coder._retry_after_s(_throttled("-1")) == 0.0
