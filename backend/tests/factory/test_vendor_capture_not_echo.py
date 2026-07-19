"""Vendored capture must not silently echo input as a successful capture."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIRROR_CAPTURE = ROOT / "backend/app/factory/vendor_blocks_mirror/capture/block.py"


def test_vendor_mirror_capture_is_real_adapter():
    text = MIRROR_CAPTURE.read_text(encoding="utf-8")
    assert "get_block" in text
    assert "Factory-generated platform block: capture" not in text
    assert '"status": "ok"' not in text or "get_block" in text
