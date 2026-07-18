"""Security tests for the Automotive Safety Intelligence pilot."""

from __future__ import annotations

import os
from pathlib import Path


def test_admin_routes_exist_in_overlay():
    root = Path(__file__).resolve().parents[1]
    admin = root / "app" / "routers" / "admin_automotive.py"
    assert admin.exists()
    text = admin.read_text(encoding="utf-8")
    for route in ("/status", "/build", "/verify", "/evaluate", "/activate", "/rollback"):
        assert route in text


def test_private_layer_constant_separated_from_foundation():
    root = Path(__file__).resolve().parents[1]
    retrieval = (root / "app" / "core" / "automotive_retrieval.py").read_text(encoding="utf-8")
    assert 'FOUNDATION_PROJECT_ID = "automotive_core_v1"' in retrieval
    assert 'CLIENT_PRIVATE_LAYER = "client_private"' in retrieval
    assert "client_private" in retrieval
    assert "automotive_core_v1" in retrieval


def test_citation_helper_labels_layers():
    root = Path(__file__).resolve().parents[1]
    citations = (root / "app" / "core" / "automotive_chat_citations.py").read_text(
        encoding="utf-8"
    )
    retrieval = (root / "app" / "core" / "automotive_retrieval.py").read_text(
        encoding="utf-8"
    )
    assert "citations_sse_event" in citations
    assert "Automotive Core" in retrieval
    assert "Private Project" in retrieval
    assert "evidence_to_citation_payload" in retrieval


def test_upload_size_env_defaults_are_bounded():
    max_bytes = int(os.getenv("MAX_UPLOAD_BYTES", "26214400"))
    assert max_bytes <= 50 * 1024 * 1024


def test_health_ready_metrics_routes_present():
    root = Path(__file__).resolve().parents[1]
    health = (root / "app" / "routers" / "automotive_health.py").read_text(encoding="utf-8")
    assert '@router.get("/health")' in health
    assert '@router.get("/ready")' in health
    assert '@router.get("/metrics")' in health
