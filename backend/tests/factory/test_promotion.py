"""S13: PILOT_READY is machine-emitted. LotDesk cannot promote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.factory.build.harvest import CONSEQUENCE, evaluate_harvest
from app.factory.build.promotion import (
    EMITTER_ID,
    evaluate_promotion,
    reject_lotdesk_promotion,
    reread_matches,
    write_evidence,
    write_reread_twin,
)
from app.factory.build.roles import _coder_route_body

ROOT = Path(__file__).resolve().parents[3]


def _write_stage(
    stages: Path,
    name: str,
    *,
    verdict: str = "PASS",
    performed: Any = None,
    extra: Dict[str, Any] | None = None,
    twin_verdict: str | None = None,
    twin_disagreements: list | None = None,
) -> None:
    stage = name.split("_", 1)[0]
    body: Dict[str, Any] = {
        "stage": stage,
        "name": name,
        "verdict": verdict,
        "first_failing_criterion": None if verdict == "PASS" else "forced",
    }
    if performed is None and stage in {"S10", "S11", "S12"}:
        if stage == "S12":
            performed = [
                "create_persists",
                "read_returns_persisted",
                "update_persists",
                "delete_persists",
                "list_only_persisted",
                "queue_item_processed",
                "refused_action_errors",
                "idempotent_duplicate_safe",
                "unauthorized_rejected",
                "missing_field_rejected",
            ]
            body["outcomes"] = {
                item: {"status": "performed", "via": "execute_action"}
                for item in performed
            }
        else:
            performed = {
                "restore_drill"
                if stage == "S10"
                else "rollback_drill": {
                    "result": "PASS",
                    "detail": "performed, not configured",
                }
            }
    if performed is not None:
        body["performed"] = performed
    if extra:
        body.update(extra)
    (stages / f"{name}.json").write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )
    twin = {
        "stage": stage,
        "name": name.split("_", 1)[-1],
        "verdict": verdict if twin_verdict is None else twin_verdict,
        "reread_of": f"{name}.json",
        "disagreements": [] if twin_disagreements is None else twin_disagreements,
    }
    (stages / f"{name}.reread.json").write_text(
        json.dumps(twin, indent=2) + "\n", encoding="utf-8"
    )


def _complete_s10_s12(stages: Path) -> None:
    _write_stage(
        stages,
        "S10_data",
        extra={
            "sqlite_on_mounted_disk": {
                "spof": "Render persistent disk is single-instance.",
            }
        },
    )
    _write_stage(
        stages,
        "S11_deploy",
        extra={
            "sqlite_on_mounted_disk": {
                "spof": "SPOF: Render rollback restarts a prior image.",
            }
        },
    )
    _write_stage(stages, "S12_domain")


def test_complete_s10_s11_s12_emits_pilot_ready_true(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is True, result
    assert result["verdict"] == "PASS"
    assert result["first_failing_criterion"] is None
    assert result["emitter"] == EMITTER_ID
    assert result["provenance"]["emitter"] == EMITTER_ID
    assert result["provenance"]["git_sha"]
    assert result["provenance"]["kernel_ownership"]["_coder_route_body_is_None"] is True
    assert result["provenance"]["kernel_ownership"]["execute_action_callable"] is True
    assert result["provenance"]["spof"]["S10"]
    assert result["provenance"]["spof"]["S11"]
    assert result["provenance"]["network_posture"]["chosen"] == "P1"
    assert result["harvest"]["verdict"] == "BLOCKED"
    assert result["harvest"]["copied_count"] == 0


def test_missing_stage_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _write_stage(stages, "S10_data")
    _write_stage(stages, "S11_deploy")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert result["verdict"] == "FAIL"
    assert "S12" in result["missing"]
    assert result["first_failing_criterion"]


def test_failed_stage_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S11_deploy", verdict="FAIL")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "S11_deploy.json" in result["failed"]
    assert "verdict_not_pass" in (result["first_failing_criterion"] or "")


def test_configured_only_stage_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(
        stages,
        "S10_data",
        performed={"restore_drill": {"status": "configured-only", "detail": "script present"}},
    )
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "configured_only" in (result["first_failing_criterion"] or "")


def test_reread_mismatch_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S12_domain", twin_verdict="FAIL")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "reread_mismatch" in (result["first_failing_criterion"] or "")


def test_reread_disagreements_are_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S10_data", twin_disagreements=["verdict drifted"])
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "reread_mismatch" in (result["first_failing_criterion"] or "")


def test_missing_reread_twin_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    (stages / "S11_deploy.reread.json").unlink()
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "reread_missing" in (result["first_failing_criterion"] or "")


def test_earlier_present_stage_mismatch_blocks_promotion(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S9_test", twin_verdict="FAIL")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "S9_test.json" in result["failed"]
    assert "S9" in result["required_stages"]


def test_lotdesk_cannot_become_pilot_ready():
    result = reject_lotdesk_promotion()
    assert result["PILOT_READY"] is False
    assert result["ok"] is False
    assert result["lotdesk"] == "fixture only; not patched"
    assert result["f1_present"] is True
    assert result["f5_present"] is True
    assert result["f6_present"] is True
    assert result["f24_present"] is True
    for code in ("F1", "F5", "F6", "F24"):
        assert code in result["codes"]
    assert result["domain"]["performed"] == []
    assert len(result["domain"]["failed"]) == 10


def test_harvest_is_honestly_blocked():
    harvest = evaluate_harvest()
    assert harvest["verdict"] == "BLOCKED"
    assert harvest["ok"] is False
    assert harvest["copied"] == []
    assert harvest["copied_count"] == 0
    assert harvest["authorized_write_path"] is False
    assert "will not receive" in harvest["consequence"]
    assert harvest["consequence"] == CONSEQUENCE
    assert harvest["unharvested_fixes"]


def test_coder_route_body_stays_none():
    assert _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_reread_matches_requires_same_verdict_and_empty_disagreements():
    evidence = {"verdict": "PASS"}
    assert reread_matches(evidence, {"verdict": "PASS", "disagreements": []}) is True
    assert reread_matches(evidence, {"verdict": "FAIL", "disagreements": []}) is False
    assert reread_matches(evidence, {"verdict": "PASS", "disagreements": ["x"]}) is False


def test_write_evidence_is_the_only_true_pilot_ready_sink(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    result = evaluate_promotion(stages)
    dest = tmp_path / "S13_promotion.json"
    write_evidence(dest, result)
    twin = write_reread_twin(dest, result)
    written = json.loads(dest.read_text(encoding="utf-8"))
    reread = json.loads(twin.read_text(encoding="utf-8"))
    assert written["PILOT_READY"] is True
    assert reread["PILOT_READY"] is True
    assert reread["disagreements"] == []
    assert reread["verdict"] == written["verdict"]


def test_in_tree_lotdesk_stays_false_and_reread_mismatches_block_full_tree():
    """In-tree S4 disagreements and S9 verdict drift — emitter must not promote."""
    lotdesk = reject_lotdesk_promotion()
    assert lotdesk["PILOT_READY"] is False
    stages = ROOT / "build" / "stages"
    assert (stages / "S10_data.json").is_file()
    assert (stages / "S11_deploy.json").is_file()
    assert (stages / "S12_domain.json").is_file()
    result = evaluate_promotion(stages)
    s9 = json.loads((stages / "S9_test.json").read_text(encoding="utf-8"))
    s9_twin = json.loads((stages / "S9_test.reread.json").read_text(encoding="utf-8"))
    s4_twin = json.loads((stages / "S4_ship_kernel.reread.json").read_text(encoding="utf-8"))
    assert s9.get("verdict") != s9_twin.get("verdict")
    assert s4_twin.get("disagreements")
    assert result["PILOT_READY"] is False
    assert "S4_ship_kernel.json" in result["failed"]
    assert "S9_test.json" in result["failed"]
