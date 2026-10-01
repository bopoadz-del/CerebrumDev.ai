"""The gate follows the prompt: it grades a build against what its brief asked
for. Universal checks apply to every platform; conditional checks apply only
when the brief declares their subject, so a build is never rejected over a
capability it never asked for (e.g. a RAG round-trip with no retrieval, or a
security scan on a declared test platform).

No brief in hand == the strictest reading (every signal raised), so a bare
re-render never silently lowers the bar.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.factory.build.acceptance_floor import (
    advisory_ids,
    brief_signals,
    check_ids,
    enforced_ids,
    is_production_grade,
    render_for_prompt,
)


def _bp(**kw):
    kw.setdefault("summary", "")
    kw.setdefault("capabilities", [])
    kw.setdefault("connectors", [])
    return SimpleNamespace(**kw)


def _cap(cid, desc="", block_ids=()):
    return SimpleNamespace(id=cid, description=desc, block_ids=list(block_ids))


def test_no_brief_is_the_strictest_reading():
    """None == every signal raised == only the static-advisory three are advisory."""
    assert advisory_ids(None) == advisory_ids()
    enf, adv = set(enforced_ids(None)), set(advisory_ids(None))
    assert enf.isdisjoint(adv)
    assert enf | adv == set(check_ids())


def test_universal_checks_apply_to_every_brief():
    core = {
        "postgres_boot_200", "ui_served_200", "authorship_floor",
        "no_token_literal", "cross_tenant_404",
    }
    for bp in (_bp(), _bp(rigor="test"), _bp(capabilities=[_cap("x")])):
        assert core <= set(enforced_ids(bp)), core - set(enforced_ids(bp))


def test_rag_check_is_skipped_when_no_retrieval_requested():
    """The automotive lesson, generalised: don't reject over un-requested RAG."""
    bp = _bp(capabilities=[_cap("ops_dashboards", "per-brand dashboards")])
    assert "rag_roundtrip_hit" in advisory_ids(bp)
    assert "retrieval" not in brief_signals(bp)


def test_rag_check_is_enforced_when_the_brief_asks_for_retrieval():
    bp = _bp(capabilities=[_cap("policy_search", "semantic search over SOPs",
                                block_ids=["rag_retrieval"])])
    assert "retrieval" in brief_signals(bp)
    assert "rag_roundtrip_hit" in enforced_ids(bp)


def test_audit_scan_is_advisory_for_a_declared_test_platform():
    assert "audit_clean" in advisory_ids(_bp(rigor="test"))
    assert "audit_clean" in advisory_ids(_bp(rigor="prototype"))


def test_audit_scan_is_enforced_for_a_production_brief():
    assert is_production_grade(_bp())  # unset grade == production
    assert "audit_clean" in enforced_ids(_bp())
    assert "audit_clean" in enforced_ids(_bp(rigor="production"))


def test_always_advisory_checks_stay_advisory_under_any_brief():
    for bp in (None, _bp(), _bp(rigor="production", connectors=["x"])):
        adv = set(advisory_ids(bp))
        assert {"one_live_connector", "backup_restore_roundtrip", "bench_p95"} <= adv


def test_the_prompt_marks_what_this_brief_does_not_require():
    proto = render_for_prompt(_bp(rigor="test"))
    assert "[not required by this brief]" in proto
    # a production brief with retrieval requires audit and rag -> fewer marks
    full = render_for_prompt(_bp(rigor="production",
                                 capabilities=[_cap("s", block_ids=["rag"])]))
    assert proto != full
