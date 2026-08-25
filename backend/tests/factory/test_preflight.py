"""S0: fingerprint factory + RoleRunner emission before a build."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.preflight import (
    EMITTER_ID,
    FACTORY_SOURCE_PATHS,
    canonical_fingerprint,
    evaluate_preflight,
    fingerprint_disagreements,
    inspect_kernel_ownership,
    reread_matches,
    write_evidence,
    write_reread_twin,
)
from app.factory.build.roles import _coder_route_body
from app.factory.build.runner import Outcome, RoleRunner
from app.factory.build_jobs import RUNNER, build_engine
from app.factory.delivery_standard import DOMAIN_PACK_FIELDS

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_preflight_records_required_keys():
    result = evaluate_preflight()
    for key in (
        "stage",
        "verdict",
        "git_sha",
        "emitter",
        "kernel_ownership",
        "emitter_identity",
        "factory_source",
        "existing_stage_files",
        "missing_modules",
        "stage_module_inventory",
    ):
        assert key in result, key
    assert result["stage"] == "S0"
    assert result["emitter"] == EMITTER_ID
    assert result["PILOT_READY"] is False
    assert result["git_sha"]
    assert result["git_sha"] != "unknown"
    assert result["kernel_ownership"]["execute_action_callable"] is True
    assert result["kernel_ownership"]["_coder_route_body_is_None"] is True
    assert result["kernel_ownership"]["ok"] is True
    assert result["emitter_identity"]["id"] == "app.factory.build.runner.RoleRunner"
    assert result["emitter_identity"]["default_engine"] == RUNNER
    assert result["emitter_identity"]["runner_is_default"] is True
    assert result["delivery_standard"]["domain_pack_field_count"] == len(
        DOMAIN_PACK_FIELDS
    )
    assert len(DOMAIN_PACK_FIELDS) == 15
    hashed = {row["path"] for row in result["factory_source"]}
    assert set(FACTORY_SOURCE_PATHS) == hashed
    assert all(row["present"] and row["sha256"] for row in result["factory_source"])
    assert isinstance(result["existing_stage_files"], list)
    assert "S13_promotion.json" in result["existing_stage_files"]


def test_preflight_names_missing_dealership_pack_and_cites_existing_s11_s12():
    result = evaluate_preflight()
    inventory = {row["stage"]: row for row in result["stage_module_inventory"]}
    assert inventory["S3"]["present"] is False
    assert "dealership" in inventory["S3"]["expected"]
    assert inventory["S11"]["present"] is True
    assert inventory["S11"]["expected"].endswith("deploy.py")
    assert inventory["S12"]["present"] is True
    assert inventory["S12"]["expected"].endswith("domain_acceptance.py")
    assert inventory["S7"]["present"] is True
    assert inventory["S8"]["present"] is True
    assert inventory["S10"]["present"] is True
    missing_stages = {row["stage"] for row in result["missing_modules"]}
    assert "S3" in missing_stages
    assert "S11" not in missing_stages
    assert "S12" not in missing_stages
    assert result["verdict"] == "PASS"
    assert result["ok"] is True


def test_coder_route_body_stays_none():
    assert _coder_route_body(None, None, None) is None
    assert inspect_kernel_ownership()["_coder_route_body_is_None"] is True
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_reread_twin_matches_and_mismatch_fails(tmp_path):
    result = evaluate_preflight()
    dest = tmp_path / "S0_preflight.json"
    write_evidence(dest, result)
    twin_path = write_reread_twin(dest, result)
    written = json.loads(dest.read_text(encoding="utf-8"))
    twin = json.loads(twin_path.read_text(encoding="utf-8"))
    assert written["verdict"] == "PASS"
    assert twin["verdict"] == "PASS"
    assert twin["disagreements"] == []
    assert reread_matches(written, twin) is True
    assert fingerprint_disagreements(result, evaluate_preflight()) == []

    tampered = dict(twin)
    tampered["verdict"] = "FAIL"
    assert reread_matches(written, tampered) is False
    tampered = dict(twin)
    tampered["disagreements"] = ["git_sha"]
    assert reread_matches(written, tampered) is False

    other = evaluate_preflight()
    other["git_sha"] = "0" * 40
    assert fingerprint_disagreements(result, other) == ["git_sha"]


def test_canonical_fingerprint_ignores_timestamps():
    a = evaluate_preflight()
    b = evaluate_preflight()
    assert canonical_fingerprint(a) == canonical_fingerprint(b)


def test_role_runner_records_preflight_before_roles(tmp_path):
    assert build_engine() == RUNNER
    out = tmp_path / "built"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    outcome = runner.run()
    assert outcome.ok, outcome.to_dict()
    preflight = runner.state["preflight"]
    assert preflight["verdict"] == "PASS"
    assert preflight["kernel_ownership_ok"] is True
    assert preflight["emitter"] == "app.factory.build.runner.RoleRunner"
    events = list(runner.ledger.events())
    notes = [e for e in events if e.kind.value == "NOTE" and "S0 preflight" in e.detail]
    assert notes, "preflight must be ledgered before roles complete"
    passed = [e for e in events if e.kind.value == "GATE_PASSED"]
    assert passed
    assert notes[0].seq < passed[0].seq


def test_failed_kernel_ownership_aborts_before_collector(tmp_path, monkeypatch):
    def _broken():
        return {
            "execute_action": "missing",
            "execute_action_callable": False,
            "_coder_route_body_is_None": False,
            "prepare_pilot_workspace": "present",
            "prepare_pilot_workspace_in_runner": True,
            "prepare_pilot_workspace_in_pilot": True,
            "ok": False,
        }

    monkeypatch.setattr(
        "app.factory.build.preflight.inspect_kernel_ownership", _broken
    )
    out = tmp_path / "aborted"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok is False
    assert outcome.outcome is Outcome.FAILED_GATE
    assert "preflight" in outcome.detail.lower()
    assert not (out / "vendor").exists()
    assert not (out / "app").exists()
