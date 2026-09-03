"""Wall-clock watchdog around factory LLM HTTP posts.

httpx's ``timeout=N`` is idle-based: a streaming or keepalive response
resets the read timer on every byte, so a reasoning model that dribbles
tokens (or an upstream that never closes) can occupy the WRITER for tens
of minutes. Live sess_3f3115ba6d764102 sat in WRITER with
"quiet for 14+ min — model call may still be running" because of this.

This helper joins a daemon thread for at most ``timeout_s``. The caller
is released even if the socket stays open. Abandoned posts cannot pin
the factory event loop: the build already runs on a background thread,
and this join returns on the deadline.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional

import httpx

CODER_TIMEOUT_ENV = "FACTORY_CODER_TIMEOUT_S"
DEFAULT_CALL_TIMEOUT_S = 120.0
#: One _llm_code_call may try primary + same-endpoint fallback +
#: cross-provider. Plus a small grace so status polling does not race the
#: last join.
MAX_LEGS = 3
MODEL_CALL_GRACE_S = 30.0


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


def post_with_deadline(
    url: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float | None = None,
    post: Optional[Callable[..., Any]] = None,
) -> httpx.Response:
    """POST with both an idle httpx timeout and a wall-clock join deadline.

    ``post`` defaults to ``httpx.post``. Callers that re-export ``httpx``
    (the coder) pass their own so existing test monkeypatches keep working.
    Tests that hang inside ``post`` still lose the caller after ``timeout``.
    """
    timeout_s = float(timeout) if timeout is not None else call_timeout_s()
    timeout_s = max(0.05, timeout_s)
    poster = post or httpx.post
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["resp"] = poster(
                url, json=json, headers=headers, timeout=timeout_s
            )
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
            box["exc"] = exc

    worker = threading.Thread(
        target=_run, name="factory-llm-watchdog", daemon=True
    )
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise httpx.ReadTimeout(
            f"coder LLM watchdog fired after {timeout_s:.1f}s "
            f"(upstream still open at {url})"
        )
    if "exc" in box:
        raise box["exc"]
    resp = box.get("resp")
    if resp is None:
        raise httpx.ReadTimeout(
            f"coder LLM watchdog got no response after {timeout_s:.1f}s"
        )
    return resp
