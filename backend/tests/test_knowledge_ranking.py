"""Knowledge retrieval ranks by the shared credibility ladder.

CERTIFIED (1) must outrank QUARANTINE (5) at equal relevance. The previous
sort used ``-(credibility_tier)``, which inverted the documented ladder
(lower int = more credible).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.blocks import _knowledge as kb
from app.core.credibility import CredibilityTier


def _temp_kb(tmp_path: Path, monkeypatch, entries):
    kb_file = tmp_path / "kb.json"
    kb_file.write_text(
        json.dumps({"schema_version": "1", "kb_version": "test", "entries": entries}),
        encoding="utf-8",
    )
    monkeypatch.setenv(kb._KB_OVERRIDE_ENV, str(kb_file))
    kb._KB_CACHE = None
    kb._KB_MTIME = 0.0
    return kb_file


def _entry(eid, tier, **extra):
    return {
        "id": eid,
        "type": "rule",
        "title": "asphalt laying temperature minimum",
        "statement": "Asphalt must be laid above the minimum temperature.",
        "credibility_tier": tier,
        "applicability": {"applies_to": ["construction.roads"]},
        **extra,
    }


def test_credibility_ladder_lower_int_is_more_credible():
    """Knowledge retrieval and the credibility module share one integer ladder."""
    assert int(CredibilityTier.CERTIFIED) == 1
    assert int(CredibilityTier.OPERATIONAL) == 2
    assert int(CredibilityTier.EXPERIMENTAL) == 3
    assert int(CredibilityTier.UNVERIFIED) == 4
    assert int(CredibilityTier.QUARANTINE) == 5
    assert CredibilityTier.CERTIFIED < CredibilityTier.QUARANTINE
    assert kb.credibility_rank({"credibility_tier": int(CredibilityTier.CERTIFIED)}) < (
        kb.credibility_rank({"credibility_tier": int(CredibilityTier.QUARANTINE)})
    )


def test_certified_ranks_before_quarantine_at_equal_relevance(tmp_path, monkeypatch):
    """Two otherwise-equal hits: CERTIFIED must return first, QUARANTINE second."""
    _temp_kb(
        tmp_path,
        monkeypatch,
        [
            _entry("roads.quarantine", int(CredibilityTier.QUARANTINE)),
            _entry("roads.certified", int(CredibilityTier.CERTIFIED)),
        ],
    )
    results = kb.search_knowledge("asphalt laying temperature minimum", top_k=2)
    assert [r["id"] for r in results] == ["roads.certified", "roads.quarantine"]
    assert results[0]["credibility_tier"] == int(CredibilityTier.CERTIFIED)
    assert results[1]["credibility_tier"] == int(CredibilityTier.QUARANTINE)


def test_equal_relevance_resolved_by_credibility_tier(tmp_path, monkeypatch):
    """At equal token overlap, lower tier int (higher authority) wins."""
    _temp_kb(
        tmp_path,
        monkeypatch,
        [
            _entry("roads.operational", int(CredibilityTier.OPERATIONAL)),
            _entry("roads.unverified", int(CredibilityTier.UNVERIFIED)),
        ],
    )
    results = kb.search_knowledge("asphalt laying temperature minimum", top_k=2)
    assert [r["id"] for r in results] == ["roads.operational", "roads.unverified"]
