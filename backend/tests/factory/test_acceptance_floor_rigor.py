"""The acceptance floor is a VARIABLE, not a fixed bar.

A customer may want a throwaway prototype, a light internal tool, or a
production platform — and the Store gate must grade each against the bar it
asked for, not one fixed maximum. Live 2026-10-01: the automotive build was
held at 20/21 by audit_clean (bandit B608) that the writer's prompt never
mentioned — a fixed check beyond the brief. The fix is that rigor is
brief-declared and both consumers (prompt + gate) read it from one source.

Default rigor is the strictest (production): absent a declared rigor, nothing
is silently lowered, so every existing build grades exactly as before.
"""

from __future__ import annotations

from app.factory.build.acceptance_floor import (
    DEFAULT_RIGOR,
    RIGOR_LEVELS,
    advisory_ids,
    check_ids,
    enforced_ids,
    render_for_prompt,
)


def test_rigor_levels_run_prototype_to_production():
    assert RIGOR_LEVELS == ("prototype", "light", "standard", "production")
    assert DEFAULT_RIGOR == "production"


def test_default_rigor_changes_nothing():
    """The whole point of a safe default: production == today's behaviour."""
    assert advisory_ids("production") == advisory_ids()
    # enforced ∪ advisory partitions the full checklist, no overlap.
    enf, adv = set(enforced_ids("production")), set(advisory_ids("production"))
    assert enf.isdisjoint(adv)
    assert enf | adv == set(check_ids())


def test_a_lower_rigor_enforces_strictly_fewer_checks():
    """Each step down the ladder may demote checks to advisory, never add."""
    prev = set(enforced_ids("production"))
    for level in ("standard", "light", "prototype"):
        cur = set(enforced_ids(level))
        assert cur <= prev, "%s enforces %s not enforced at the stricter level" % (
            level,
            cur - prev,
        )
        prev = cur


def test_a_prototype_does_not_enforce_audit_clean():
    """A throwaway must not die on a security scan it never asked for."""
    assert "audit_clean" in advisory_ids("prototype")
    assert "audit_clean" in enforced_ids("production")


def test_a_prototype_still_proves_it_works():
    """Even the lowest bar keeps the 'is it real' core enforced."""
    core = {"postgres_boot_200", "ui_served_200", "authorship_floor"}
    assert core <= set(enforced_ids("prototype")), core - set(enforced_ids("prototype"))


def test_always_advisory_checks_stay_advisory_at_every_rigor():
    """one_live_connector/backup/bench need pipeline evidence nothing emits —
    they are advisory regardless of how strict the brief asks to be."""
    for level in RIGOR_LEVELS:
        adv = set(advisory_ids(level))
        assert {"one_live_connector", "backup_restore_roundtrip", "bench_p95"} <= adv


def test_the_prompt_states_the_active_rigor():
    proto = render_for_prompt("prototype")
    prod = render_for_prompt("production")
    assert "prototype" in proto.lower()
    assert "production" in prod.lower()
    # the two renders differ — the writer is told a different bar
    assert proto != prod


def test_an_unknown_rigor_is_refused_not_silently_widened():
    import pytest

    with pytest.raises(ValueError):
        enforced_ids("ultra")


# ── the rigor reaches the stamped harness and the writer prompt ─────────────

def test_the_rendered_harness_follows_the_build_rigor():
    from app.factory.build.store_acceptance import render_acceptance_script

    prod = render_acceptance_script("production")
    proto = render_acceptance_script("prototype")
    # audit_clean vetoes at production, is advisory at prototype
    assert "'audit_clean'" not in _advisory_list(prod)
    assert "'audit_clean'" in _advisory_list(proto)
    # the full checklist is unchanged — all checks still RUN and report
    assert prod.count("CHECKS = [") == 1 and proto.count("CHECKS = [") == 1


def test_a_bad_rigor_value_defaults_strict_not_crash():
    from app.factory.build.store_acceptance import render_acceptance_script

    assert render_acceptance_script("half-ass") == render_acceptance_script("production")


def test_the_writer_prompt_states_the_blueprint_rigor():
    from types import SimpleNamespace
    from app.factory.build.writer_prompt import render_writer_prompt

    bp = SimpleNamespace(product_id="p", product_name="P", vertical="v",
                         summary="s", rigor="prototype")
    assert "rigor=prototype" in render_writer_prompt(bp, brief="x")


def test_an_unset_blueprint_rigor_is_production():
    from types import SimpleNamespace
    from app.factory.build.writer_prompt import render_writer_prompt

    bp = SimpleNamespace(product_id="p", product_name="P", vertical="v", summary="s")
    assert "rigor=production" in render_writer_prompt(bp, brief="x")


def _advisory_list(script: str) -> str:
    import re
    m = re.search(r"^ADVISORY = \[(.*?)\]", script, re.MULTILINE)
    return m.group(1) if m else ""
