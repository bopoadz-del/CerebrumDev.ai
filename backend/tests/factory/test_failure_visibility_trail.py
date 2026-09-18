"""F1/F3: every failure carries a named reason token, and build-status
exposes the per-phase trail — not one concatenated string.

The acceptance shape: from build-status alone, an operator (and the Floor)
must be able to say which phase failed, why, and where — no source reading.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles_models import RoleError, RoleResult
from app.factory.build_jobs import _phase_trail, build_status


def _started_ledger(tmp_path: Path) -> BuildLedger:
    out = tmp_path / "build"
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="probe", inputs_hash="abc")
    return ledger


class TestPhaseTrail:
    def test_a_gate_failure_lands_as_a_named_row_and_failure(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.WRITER,
            detail="writer_no_output: zero agent-authored artifacts",
            payload={
                "gate": "writer_contract",
                "reason": "writer_no_output",
                "location": "WRITER",
            },
        )

        trail, failure = _phase_trail(ledger.events())

        writer = next(row for row in trail if row["phase"] == "WRITER")
        assert writer["outcome"] == "failed"
        assert writer["reason"] == "writer_no_output"
        assert writer["location"] == "WRITER"
        assert failure is not None
        assert failure["phase"] == "WRITER"
        assert failure["reason"] == "writer_no_output"

    def test_an_aborted_phase_is_distinguished_from_a_gate_failure(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
        ledger.append(
            EventKind.PHASE_ABORTED,
            role=BuildRole.WRITER,
            detail="codewhale_worker_failed: worker exited 1",
            payload={"reason": "codewhale_worker_failed", "location": "WRITER"},
        )

        trail, failure = _phase_trail(ledger.events())

        writer = next(row for row in trail if row["phase"] == "WRITER")
        assert writer["outcome"] == "aborted"
        assert writer["reason"] == "codewhale_worker_failed"
        assert failure["reason"] == "codewhale_worker_failed"

    def test_unreached_phases_are_marked_not_reached(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.COLLECTOR, detail="ok")

        trail, failure = _phase_trail(ledger.events())

        assert trail[0]["phase"] == "COLLECTOR"
        assert trail[0]["outcome"] == "passed"
        assert all(
            row["outcome"] == "not_reached"
            for row in trail
            if row["phase"] != "COLLECTOR"
        )
        assert failure is None


class TestBuildStatusCarriesTheTrail:
    def test_failed_build_status_exposes_failure_and_trail(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.WRITER,
            detail="writer_no_output: zero agent-authored artifacts",
            payload={
                "gate": "writer_contract",
                "reason": "writer_no_output",
                "location": "WRITER",
            },
        )

        status = build_status(tmp_path / "build")

        assert status["failure"]["location"] == "WRITER"
        assert status["failure"]["reason"] == "writer_no_output"
        assert any(
            row["phase"] == "WRITER" and row["outcome"] == "failed"
            for row in status["phase_trail"]
        )

    def test_status_redacts_keys_and_headers_from_note_activity(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail="Authorization: Bearer abc123 def456",
            payload={"stage": "handlers", "done": 1, "total": 2},
        )

        status = build_status(tmp_path / "build")

        assert "Bearer" not in status.get("activity", "")
        assert "[redacted]" in status.get("activity", "")


class TestCrashFailureNaming:
    def test_a_crashed_thread_names_the_phase_it_died_in(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(
            EventKind.RUN_FAILED,
            detail="build thread crashed; see service logs",
            payload={"reason": "build_thread_crashed", "location": "TESTER"},
        )

        status = build_status(tmp_path / "build")

        assert status["state"] == "failed"
        assert status["failure"]["phase"] == "TESTER"
        assert status["failure"]["reason"] == "build_thread_crashed"
        tester = next(
            row for row in status["phase_trail"] if row["phase"] == "TESTER"
        )
        assert tester["outcome"] == "aborted"

    def test_the_crash_exception_text_reaches_the_floor(self, tmp_path):
        """The crash handler stamps the real exception; the Floor shows it."""
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(
            EventKind.RUN_FAILED,
            detail="build thread crashed: UnicodeDecodeError: 'utf-8' codec",
            payload={
                "reason": "build_thread_crashed",
                "location": "TESTER",
                "exception": "UnicodeDecodeError",
            },
        )

        status = build_status(tmp_path / "build")

        assert status["failure"]["reason"] == "build_thread_crashed"
        assert "UnicodeDecodeError" in status["failure"]["detail"]
        assert "UnicodeDecodeError" in status["detail"]

    def test_a_crash_marker_without_terminal_event_still_names_the_phase(self, tmp_path):
        from app.factory.build_jobs import CRASH_MARKER_NAME

        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        (tmp_path / "build" / CRASH_MARKER_NAME).write_text(
            "boom", encoding="utf-8"
        )

        status = build_status(tmp_path / "build")

        assert status["state"] == "failed"
        assert status["failure"]["phase"] == "TESTER"
        assert status["failure"]["reason"] == "build_thread_crashed"

    def test_a_normally_failed_build_keeps_its_gate_reason(self, tmp_path):
        """The synthesis must not overwrite a real gate failure."""
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.WRITER,
            detail="writer_no_output: zero agent-authored artifacts",
            payload={
                "gate": "writer_contract",
                "reason": "writer_no_output",
                "location": "WRITER",
            },
        )
        ledger.append(
            EventKind.RUN_FAILED,
            detail="writer failed",
            payload={"reason": "writer_no_output", "location": "WRITER"},
        )

        status = build_status(tmp_path / "build")

        assert status["failure"]["reason"] == "writer_no_output"


class TestRoleFailureShape:
    def test_role_error_carries_reason_and_location(self):
        exc = RoleError("boom", reason="codewhale_worker_failed", location="WRITER")
        assert exc.reason == "codewhale_worker_failed"
        assert exc.location == "WRITER"
        assert "boom" in str(exc)

    def test_role_result_defaults_are_reasonless_not_broken(self):
        res = RoleResult(ok=False, detail="nope")
        assert res.reason == ""
        assert res.location == ""


class TestReworkedPhaseCarriesItsOwnVerdict:
    """A phase that failed, was reworked, and then passed reads as passed.

    Observed on a live build: TESTER and STORE_MANAGER both rendered
    ``outcome: passed`` beside the failure sentence that had re-opened
    them -- "the round-trip check ran and decided nothing, which is not a
    pass" sitting next to a green phase. The gates were fail-closed and
    working; the trail was reporting the wrong round.
    """

    def test_a_reworked_phase_drops_the_failure_that_reopened_it(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.TESTER,
            detail=(
                "PRODUCT (one-record round-trip): no capability was judgeable "
                "- the round-trip check ran and decided nothing, which is "
                "not a pass"
            ),
            payload={
                "gate": "product_round_trip",
                "reason": "round_trip_unjudged",
                "location": "TESTER",
            },
        )
        # WRITER reworks, TESTER re-opens and passes.
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(
            EventKind.GATE_PASSED,
            role=BuildRole.TESTER,
            detail="PRODUCT: every capability round-tripped a record",
        )

        trail, _ = _phase_trail(ledger.events())
        tester = next(row for row in trail if row["phase"] == "TESTER")

        assert tester["outcome"] == "passed"
        assert tester["reason"] == "", tester
        assert tester["location"] == "", tester
        assert "not a pass" not in tester["detail"], tester["detail"]
        assert "round_trip_unjudged" not in tester["detail"], tester["detail"]
        assert "round-tripped a record" in tester["detail"], tester["detail"]

    def test_the_first_failure_is_still_reported_after_a_later_pass(self, tmp_path):
        """Clearing the row must not cost failure visibility."""
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.TESTER,
            detail="suite is red",
            payload={"reason": "suite_red", "location": "TESTER"},
        )
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.TESTER, detail="suite green")

        trail, failure = _phase_trail(ledger.events())

        assert failure is not None, "the failure that happened must still be reported"
        assert failure["reason"] == "suite_red"
        assert failure["phase"] == "TESTER"
        tester = next(row for row in trail if row["phase"] == "TESTER")
        assert tester["outcome"] == "passed"

    def test_a_running_phase_does_not_wear_its_previous_failure(self, tmp_path):
        ledger = _started_ledger(tmp_path)
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
        ledger.append(
            EventKind.GATE_FAILED,
            role=BuildRole.WRITER,
            detail="writer_no_output: zero agent-authored artifacts",
            payload={"reason": "writer_no_output", "location": "WRITER"},
        )
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")

        trail, failure = _phase_trail(ledger.events())
        writer = next(row for row in trail if row["phase"] == "WRITER")

        assert writer["outcome"] == "running"
        assert writer["reason"] == "", writer
        assert writer["detail"] == "", writer
        assert failure is not None and failure["reason"] == "writer_no_output"
