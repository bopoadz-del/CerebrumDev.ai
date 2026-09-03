"""Wall-clock watchdog around factory LLM HTTP posts.

httpx's ``timeout=N`` is idle-based: a streaming or keepalive response
resets the read timer on every byte, so a reasoning model that dribbles
tokens (or an upstream that never closes) can occupy the WRITER for tens
of minutes. Live sess_3f3115ba6d764102 sat in WRITER with
"quiet for 14+ min — model call may still be running" because of this.

#291 joined a daemon thread for ``timeout_s``. That releases the caller
ONLY if ``Thread.join(timeout)`` returns. Live sess_97a1bc6525924e8b
waited 1965s against a 480s attempt wall (production
``FACTORY_CODER_TIMEOUT_S=150`` × 3 legs + 30s grace): join was waited
without cancelling the socket, so a hung httpx/ssl/anyio thread pinned
the WRITER until the upstream finally dropped.

This helper:

* waits on ``Event.wait(timeout)`` — never ``join`` after the deadline
* closes the ``httpx.Client`` so the in-flight socket is cancelled
* honours an optional monotonic ``deadline`` so stacked legs cannot
  outrun ``attempt_wall_s()``
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional

import httpx

CODER_TIMEOUT_ENV = "FACTORY_CODER_TIMEOUT_S"
DEFAULT_CALL_TIMEOUT_S = 120.0
#: One _llm_code_call may try primary + same-endpoint fallback +
#: cross-provider. Plus a small grace so status polling does not race the
#: last attempt.
MAX_LEGS = 3
MODEL_CALL_GRACE_S = 30.0
#: Caller must be released within this much of the deadline. Live 1965s
#: vs 480s was ~4×; a hung post that overshoots by this much is still a
#: bug in the watchdog, not "the model is thinking".
WATCHDOG_OVERSHOOT_GRACE_S = 8.0


def call_timeout_s() -> float:
    """Per-attempt ceiling for one coder HTTP request.

    Override with FACTORY_CODER_TIMEOUT_S. An explicit value is honoured
    (minimum 0.05s) so tests can use a short deadline; the unset default
    is 120s — long enough for a reasoning model, short enough that a hang
    cannot look like "still working".
    """
    raw = os.getenv(CODER_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_CALL_TIMEOUT_S
    try:
        return max(0.05, float(raw))
    except ValueError:
        return DEFAULT_CALL_TIMEOUT_S


def attempt_wall_s() -> float:
    """Ceiling for one coder call including alternate-model retries."""
    return call_timeout_s() * MAX_LEGS + MODEL_CALL_GRACE_S


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return True
    text = str(exc).lower()
    return "timed out" in text or "watchdog" in text


def _is_real_httpx_post(fn: Callable[..., Any]) -> bool:
    """True only for the library function, not a test monkeypatch."""
    return (
        getattr(fn, "__module__", "") == "httpx"
        and getattr(fn, "__name__", "") == "post"
    )


def remaining_timeout_s(
    timeout_s: float, deadline_mono: Optional[float]
) -> float:
    """Cap one post so stacked legs cannot outrun a shared attempt wall."""
    cap = max(0.05, float(timeout_s))
    if deadline_mono is None:
        return cap
    left = deadline_mono - time.monotonic()
    if left < 0.05:
        raise httpx.ReadTimeout(
            f"coder LLM watchdog: attempt deadline already passed "
            f"({left:.2f}s left)"
        )
    return min(cap, left)


def _timeout_error(url: str, timeout_s: float) -> httpx.ReadTimeout:
    return httpx.ReadTimeout(
        f"coder LLM watchdog fired after {timeout_s:.1f}s "
        f"(upstream still open at {url})"
    )


def post_with_deadline(
    url: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float | None = None,
    post: Optional[Callable[..., Any]] = None,
    deadline_mono: Optional[float] = None,
) -> httpx.Response:
    """POST with an idle httpx timeout and a hard wall-clock deadline.

    ``post`` defaults to ``httpx.post``. Callers that re-export ``httpx``
    (the coder) pass their own so existing test monkeypatches keep working.

    The caller is released when ``Event.wait`` hits ``timeout``, even if
    ``Thread.join`` would block for the abandoned worker. A real httpx
    post is cancelled by closing the Client so a streaming/keepalive
    socket cannot keep the WRITER busy past the wall.
    """
    timeout_s = remaining_timeout_s(
        float(timeout) if timeout is not None else call_timeout_s(),
        deadline_mono,
    )
    poster = post or httpx.post
    box: Dict[str, Any] = {}
    client_holder: Dict[str, Any] = {}
    done = threading.Event()

    def _cancel() -> None:
        client = client_holder.get("client")
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001 — best-effort socket cancel
            pass

    def _run() -> None:
        try:
            if _is_real_httpx_post(poster):
                client = httpx.Client(timeout=timeout_s)
                client_holder["client"] = client
                try:
                    box["resp"] = client.post(
                        url, json=json, headers=headers
                    )
                finally:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                box["resp"] = poster(
                    url, json=json, headers=headers, timeout=timeout_s
                )
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
            box["exc"] = exc
        finally:
            done.set()

    worker = threading.Thread(
        target=_run, name="factory-llm-watchdog", daemon=True
    )
    worker.start()
    # Never join after the deadline. join(timeout) is how live 1965s>480s
    # happened: the worker held the caller until the socket died.
    if not done.wait(timeout_s):
        _cancel()
        raise _timeout_error(url, timeout_s)
    if "exc" in box:
        raise box["exc"]
    resp = box.get("resp")
    if resp is None:
        raise httpx.ReadTimeout(
            f"coder LLM watchdog got no response after {timeout_s:.1f}s"
        )
    if deadline_mono is not None and time.monotonic() >= deadline_mono:
        raise _timeout_error(url, timeout_s)
    return resp
