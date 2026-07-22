"""Tiny in-memory rate limiter for public auth endpoints — zero deps.

Single-process (the Render web service runs one process for us). Move the
buckets to Redis when the factory scales past one instance.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

_LOCK = threading.Lock()
_BUCKETS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _limits() -> tuple[int, int]:
    try:
        max_attempts = int(os.getenv("AUTH_RATE_LIMIT_MAX", "10"))
    except ValueError:
        max_attempts = 10
    try:
        window = int(os.getenv("AUTH_RATE_LIMIT_WINDOW_S", "600"))
    except ValueError:
        window = 600
    return max_attempts, window


def check_rate_limit(bucket: str, key: str) -> bool:
    """Record an attempt; False when the bucket is full (caller returns 429)."""
    max_attempts, window = _limits()
    now = time.monotonic()
    with _LOCK:
        q = _BUCKETS[(bucket, key)]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= max_attempts:
            return False
        q.append(now)
        return True


def reset_rate_limits() -> None:
    """Clear all buckets (test isolation)."""
    with _LOCK:
        _BUCKETS.clear()
