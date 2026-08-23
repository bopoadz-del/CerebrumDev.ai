"""A two-hour build must survive an interrupt and be honest afterwards.

New-shape tests for the manufacturing ledger. The failures these guard
against are the ones that make a long run untrustworthy rather than broken:
a resume that silently continues against a changed blueprint, a phase that
counts as done because it passed once before being aborted, and a run that
converged after eleven writer/tester round trips but reads like it passed
first try.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import (
    BuildLedger,
    EventKind,
    LedgerError,
    iter_ledgers,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> BuildLedger:
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"2026-08-11T00:00:{counter['n']:02d}+00:00"

    return BuildLedger(tmp_path / "run" / "build_ledger.jsonl", clock=clock)


def _pass(ledger: BuildLedger, *roles: BuildRole) -> None:
    for role in roles:
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role)


def test_ledger_is_created_on_first_append_and_is_append_only(ledger):
    assert not ledger.exists()
    ledger.start_run(product_id="retail_ops", inputs_hash="abc123def456")
    ledger.append(EventKind.NOTE, detail="second")

    lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["seq"] for x in lines] == [1, 2]
    # The first line is untouched by the second write.
    assert json.loads(lines[0])["payload"]["product_id"] == "retail_ops"


def test_resume_point_walks_the_phase_order(ledger):
    ledger.start_run(product_id="p", inputs_hash="h")
    assert ledger.resume_point() is BuildRole.COLLECTOR

    _pass(ledger, BuildRole.COLLECTOR, BuildRole.CLONER)
    assert ledger.resume_point() is BuildRole.WRITER
    assert ledger.completed_roles() == {BuildRole.COLLECTOR, BuildRole.CLONER}


def test_run_is_complete_only_when_every_phase_passed(ledger):
    _pass(
        ledger,
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
    )
    assert ledger.resume_point() is BuildRole.STORE_MANAGER
    assert ledger.summary()["complete"] is False

    _pass(ledger, BuildRole.STORE_MANAGER)
    assert ledger.resume_point() is None
    assert ledger.summary()["complete"] is True


def test_a_failed_phase_does_not_count_as_done(ledger):
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.COLLECTOR)
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.COLLECTOR, detail="block missing")
    assert ledger.completed_roles() == set()
    assert ledger.resume_point() is BuildRole.COLLECTOR


def test_latest_verdict_wins_in_both_directions(ledger):
    """Retry-then-pass counts; pass-then-abort does not."""
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.WRITER)
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER)
    assert BuildRole.WRITER in ledger.completed_roles()

    ledger.append(EventKind.GATE_PASSED, role=BuildRole.TESTER)
    ledger.append(EventKind.PHASE_ABORTED, role=BuildRole.TESTER, detail="interrupted")
    assert BuildRole.TESTER not in ledger.completed_roles()


def test_resume_against_changed_inputs_is_refused(ledger):
    ledger.start_run(product_id="p", inputs_hash="aaaaaaaaaaaa")
    ledger.assert_resumable(inputs_hash="aaaaaaaaaaaa")

    with pytest.raises(LedgerError, match="cannot resume"):
        ledger.assert_resumable(inputs_hash="bbbbbbbbbbbb")


def test_fresh_ledger_resumes_against_anything(ledger):
    """No RUN_STARTED yet means nothing to contradict."""
    ledger.assert_resumable(inputs_hash="whatever")


def test_rework_rounds_are_visible_in_the_summary(ledger):
    for _ in range(3):
        ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="tests red")
    assert ledger.rework_counts() == {"WRITER": 3}
    assert ledger.summary()["rework"] == {"WRITER": 3}


def test_clones_are_registered_for_the_store_registrar(ledger):
    ledger.record_clone(
        block_id="invoice_parser",
        source_commit="0123456789abcdef",
        store_repo="Cerebrum-Blocks",
        vendored_path="vendor/blocks/invoice_parser",
    )
    ledger.record_clone(
        block_id="web",
        source_commit="fedcba9876543210",
        store_repo="Cerebrum-Blocks",
        vendored_path="vendor/blocks/web",
    )

    clones = ledger.clones()
    assert [c["block_id"] for c in clones] == ["invoice_parser", "web"]
    assert clones[0]["source_commit"] == "0123456789abcdef"


def test_re_cloning_a_block_reports_the_latest_commit_only(ledger):
    """A rebuilt platform has one current version per block, not a history."""
    ledger.record_clone(
        block_id="web", source_commit="a" * 16, store_repo="S", vendored_path="v/web"
    )
    ledger.record_clone(
        block_id="web", source_commit="b" * 16, store_repo="S", vendored_path="v/web"
    )

    clones = ledger.clones()
    assert len(clones) == 1
    assert clones[0]["source_commit"] == "b" * 16


def test_corrupt_ledger_raises_rather_than_silently_losing_history(tmp_path):
    path = tmp_path / "build_ledger.jsonl"
    path.write_text('{"seq": 1, "kind": "NOTE"}\nnot json at all\n', encoding="utf-8")

    with pytest.raises(LedgerError, match="readable ledger event"):
        BuildLedger(path).events()


def test_registrar_can_scan_every_platform_ledger(tmp_path):
    for name in ("alpha", "beta"):
        led = BuildLedger(tmp_path / name / "build_ledger.jsonl")
        led.start_run(product_id=name, inputs_hash=name * 4)
        led.record_clone(
            block_id="web", source_commit="c" * 16, store_repo="S", vendored_path="v/web"
        )

    found = {led.summary()["inputs_hash"]: led.clones() for led in iter_ledgers(tmp_path)}
    assert set(found) == {"alpha" * 4, "beta" * 4}
    assert all(c[0]["block_id"] == "web" for c in found.values())


def test_scanning_a_missing_root_is_empty_not_an_error(tmp_path):
    assert list(iter_ledgers(tmp_path / "nope")) == []


def test_pilot_cycle_reopens_tester_and_store_without_wiping_writer(ledger):
    ledger.start_run(product_id="used-cars", inputs_hash="h")
    _pass(
        ledger,
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
        BuildRole.STORE_MANAGER,
    )
    ledger.append(EventKind.RUN_SUCCEEDED, detail="all phase gates passed")
    assert ledger.succeeded() is True
    assert ledger.pilot_ready() is False
    assert ledger.resume_point() is None

    ledger.open_pilot_cycle()
    assert ledger.succeeded() is False
    assert ledger.pilot_cycle_open() is True
    assert ledger.code_phase_succeeded() is True
    done = ledger.completed_roles()
    assert BuildRole.WRITER in done
    assert BuildRole.CLONER in done
    assert BuildRole.TESTER not in done
    assert BuildRole.STORE_MANAGER not in done
    assert ledger.resume_point() is BuildRole.TESTER

    _pass(ledger, BuildRole.TESTER, BuildRole.STORE_MANAGER)
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="all phase gates passed",
        payload={"cycle": "pilot", "pilot_ready": True},
    )
    assert ledger.succeeded() is True
    assert ledger.pilot_ready() is True
    assert ledger.pilot_cycle_open() is False
