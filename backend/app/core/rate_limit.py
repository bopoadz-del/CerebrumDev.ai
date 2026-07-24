"""Rate limiter for public auth endpoints.

Backends, in order of preference:
1. Redis (``REDIS_URL`` set) — buckets shared across instances and restarts.
2. In-memory deque buckets (default) — zero deps, single-process.

Redis failures fall back to the in-memory bucket so an outage never locks
users out.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

_LOCK = threading.Lock()
_BUCKETS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

_REDIS_UNSET = object()
_redis_client: object = _REDIS_UNSET


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


def _redis():
    """Lazy Redis client; None when unconfigured or unreachable."""
    global _redis_client
    if _redis_client is _REDIS_UNSET:
        url = os.getenv("REDIS_URL", "").strip()
        client = None
        if url:
            try:
                import redis  # type: ignore

                client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
                client.ping()
            except Exception:
                client = None
        _redis_client = client
    return _redis_client


def check_rate_limit(bucket: str, key: str) -> bool:
    """Record an attempt; False when the bucket is full (caller returns 429)."""
    max_attempts, window = _limits()
    client = _redis()
    if client is not None:
        try:
            slot = int(time.time() // window)
            rkey = f"rl:{bucket}:{key}:{slot}"
            count = client.incr(rkey)
            if count == 1:
                client.expire(rkey, window * 2)
            return count <= max_attempts
        except Exception:
            pass  # fall back to the in-memory bucket on Redis errors
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
