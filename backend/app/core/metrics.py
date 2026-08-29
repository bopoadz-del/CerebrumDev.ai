"""Prometheus ``/metrics`` payload.

The scrape is an ops surface, same class as ``POST /v1/ops/backup``: the
route is wired with ``require_master_key`` in ``main``. This module only
builds the text. Default process/platform collectors are enough for a first
scrape; request histograms stay out until something actually pages on them.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from starlette.responses import Response

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


def render_metrics() -> bytes:
    """Build a fresh registry so tests and scrapes do not share counters."""
    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    Gauge(
        "cerebrumdev_up",
        "CerebrumDev.ai factory process is up",
        registry=registry,
    ).set(1)
    return generate_latest(registry)


def metrics_response() -> Response:
    return Response(content=render_metrics(), media_type=METRICS_CONTENT_TYPE)
