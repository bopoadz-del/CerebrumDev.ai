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
