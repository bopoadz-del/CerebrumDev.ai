"""Blocks REUSE HTTP is feature-detected; local dual-registry is the fallback."""

from __future__ import annotations

from app.factory.build.reuse_lookup import (
    ReuseRecord,
    lookup_reuse,
    parse_reuse_body,
    resolve_store_presence,
)


def test_parse_always_200_present_true_pulls_scope():
    rec = parse_reuse_body(
        "event_bus",
        {
            "present": True,
            "reuse": True,
            "reads": ["topic"],
            "writes": ["event"],
            "never": ["channel"],
            "acceptance": ["publish succeeds"],
        },
        source="registry/blocks",
    )
    assert rec.present is True
    assert rec.reads == ["topic"]
    assert rec.writes == ["event"]
    assert rec.never == ["channel"]
    assert rec.acceptance == ["publish succeeds"]


def test_parse_present_false_is_not_assumed():
    rec = parse_reuse_body("nope", {"present": False}, source="registry/blocks")
    assert rec.present is False


def test_http_unavailable_falls_back_to_local_ids():
    rec = lookup_reuse(
        "event_bus",
        local_ids={"event_bus", "database"},
        base_url="",
    )
    assert rec.present is True
    assert rec.source == "local_dual_registry"
    missing = lookup_reuse("not_a_block", local_ids={"event_bus"}, base_url="")
    assert missing.present is False


def test_http_200_wins_over_local_absence():
    def fake_get(block_id, base_url=None):
        return ReuseRecord(
            block_id=block_id,
            present=True,
            source="registry/blocks",
            reads=["payload"],
        )

    rec = lookup_reuse(
        "remote_only",
        local_ids=set(),
        base_url="https://blocks.example",
        http_get=fake_get,
    )
    assert rec.present is True
    assert rec.reads == ["payload"]


def test_resolve_maps_claimed_ids():
    records = resolve_store_presence(
        ["event_bus", "ghost"],
        local_ids={"event_bus"},
        base_url="",
    )
    assert records["event_bus"].present is True
    assert records["ghost"].present is False
