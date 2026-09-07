"""Launching-ready full-pilot authorship floor (≥5 action handlers).

VetCare Hub sess_cec9a1345b2049bb (action_py=3) and residential-lettings
(action_py=4) were STORE_GREEN + package 200. written=0 stays
FACTORY_CODE_CLI_NO_AUTHORSHIP. Kit/Steward-shaped ≥5 stays exportable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.session_store import create_session, get_session, update_session
from app.factory.build.authorship import (
    FULL_PILOT_MIN_AUTHORED_ACTIONS,
    full_pilot_authorship_acceptance_line,
    full_pilot_authorship_forbidden_lines,
    full_pilot_authorship_from,
    full_pilot_authorship_needles,
    full_pilot_authorship_rules_text,
    is_action_artifact_id,
    thin_store_green_export_blocker,
)
from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.level_grade import Level, attach_level_grade
from app.factory.build_jobs import _authorship, build_status
from app.factory.product_architect import plan_blueprint
from app.main import app
from fastapi.testclient import TestClient
from tests.factory.test_level_grade import _full_repo


VETCARE_THIN = ("audit", "vetcare_hub_veterinary_core", "workflow")
STEWARD_FIVE = ("audit", "workflow", "team", "document_engine", "validation")


def test_action_artifact_id_skips_models_routes_and_extras():
    assert is_action_artifact_id("audit") is True
    assert is_action_artifact_id("model:audit") is False
    assert is_action_artifact_id("route:audit") is False
    assert is_action_artifact_id("readme") is False
    assert is_action_artifact_id("template_0.py") is False


def test_floor_constant_is_five():
    """Mutation: a silent drop to 0/1 would re-open thin Store-green."""
    assert FULL_PILOT_MIN_AUTHORED_ACTIONS == 5
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    assert f"≥{n}" in full_pilot_authorship_rules_text()
    assert f"≥{n}" in full_pilot_authorship_acceptance_line()
    assert f"<{n}" in full_pilot_authorship_forbidden_lines()
    assert f"≥{n}" in full_pilot_authorship_needles()


def test_compiled_cbrief_states_the_same_floor_constant():
    """Coder brief must name the #387 refuse — not just thin SUCCESS prose."""
    root = Path(__file__).resolve().parents[3]
    compiled = compile_brief(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        plan_blueprint(load_blueprint(root / "blueprints/examples/runner_smoke.yaml")),
    )
    text = compiled.text
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    assert full_pilot_authorship_rules_text() in text
    assert full_pilot_authorship_acceptance_line() in text
    assert full_pilot_authorship_forbidden_lines() in text
    for needle in full_pilot_authorship_needles():
        assert needle in text
    assert f"≥{n}" in text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_vetcare_three_actions_are_below_floor():
    snap = full_pilot_authorship_from(
        {
            "authorship": {
                "agent_written": 3,
                "action_py": 3,
                "agent_artifacts": list(VETCARE_THIN),
                "cli_authored_ids": list(VETCARE_THIN),
            }
        }
    )
    assert snap.action_py == 3
    assert snap.measured is True
    assert snap.meets_floor is False
    assert snap.below_floor is True


def test_four_cli_ids_are_below_floor_five_meet():
    four = full_pilot_authorship_from(
        {"authorship": {"cli_authored_ids": list(STEWARD_FIVE[:4])}}
    )
    five = full_pilot_authorship_from(
        {"authorship": {"cli_authored_ids": list(STEWARD_FIVE)}}
    )
    assert four.below_floor is True
    assert five.meets_floor is True
    assert five.below_floor is False


def test_unmeasured_authorship_is_not_a_silent_pass_or_refuse():
    snap = full_pilot_authorship_from({"pilot_ready": True})
    assert snap.measured is False
    assert snap.meets_floor is False
    assert snap.below_floor is False


def test_export_blocker_refuses_thin_pilot_not_code_cycle():
    thin = {
        "cycle": "pilot",
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"agent_written": 3, "cli_authored_ids": list(VETCARE_THIN)},
    }
    blocked = thin_store_green_export_blocker(thin)
    assert blocked
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in blocked
    assert "full-pilot" in blocked

    prototype = {
        "cycle": "code",
        "detail": "CODE PASS — x; PRODUCT NOT RUN — y; STORE NOT RUN — z",
        "authorship": {"agent_written": 3, "cli_authored_ids": list(VETCARE_THIN)},
    }
    assert thin_store_green_export_blocker(prototype) is None

    healthy = {
        "cycle": "pilot",
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"agent_written": 5, "cli_authored_ids": list(STEWARD_FIVE)},
    }
    assert thin_store_green_export_blocker(healthy) is None


def _succeeded_pilot(out: Path, *, product_id: str) -> None:
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product_id, inputs_hash="floor-hash")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        role=BuildRole.STORE_MANAGER,
        detail="CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
        payload={"outcome": "SUCCESS", "cycle": "pilot", "pilot_ready": True},
    )


def _write_provenance(out: Path, action_ids: tuple[str, ...]) -> None:
    docs = out / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    sources = {cid: "coder CLI (/usr/local/bin/kimi)" for cid in action_ids}
    sources["readme"] = "deterministic contract template"
    (docs / "build_provenance.json").write_text(
        json.dumps(
            {
                "artifact_sources": sources,
                "brief_dispatch": {
                    "via": "cli",
                    "ok": True,
                    "cli_authored_ids": list(action_ids),
                },
                "coder_failures": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_authorship_status_exposes_action_py_and_cli_ids(tmp_path):
    out = tmp_path / "vetcare-hub"
    out.mkdir()
    _write_provenance(out, VETCARE_THIN)
    auth = _authorship(out)["authorship"]
    assert auth["action_py"] == 3
    assert auth["agent_written"] == 3
    assert set(auth["cli_authored_ids"]) == set(VETCARE_THIN)
    assert set(auth["agent_artifacts"]) == set(VETCARE_THIN)


def test_build_status_demotes_thin_pilot_ready(tmp_path):
    out = tmp_path / "vetcare-hub"
    _full_repo(out)
    _succeeded_pilot(out, product_id="vetcare-hub")
    _write_provenance(out, VETCARE_THIN)
    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is False
    assert status["level_grade"]["level"] != Level.STORE_GREEN.value
    assert status["level_grade"]["full_pilot"] is False
    assert status["authorship"]["action_py"] == 3


def test_build_status_keeps_steward_shaped_five(tmp_path):
    out = tmp_path / "cerebrum-steward"
    _full_repo(out)
    _succeeded_pilot(out, product_id="cerebrum-steward")
    _write_provenance(out, STEWARD_FIVE)
    status = build_status(out)
    assert status["pilot_ready"] is True
    assert status["level_grade"]["level"] in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert status["level_grade"]["full_pilot"] is True
    assert status["authorship"]["action_py"] == 5


def test_attach_level_grade_mutation_cannot_keep_store_green_when_thin(tmp_path):
    _full_repo(tmp_path)
    status = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": True,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"agent_written": 4, "cli_authored_ids": list(STEWARD_FIVE[:4])},
    }
    attached = attach_level_grade(status, tmp_path)
    assert attached["pilot_ready"] is False
    assert attached["level_grade"]["full_pilot"] is False
    assert attached["level_grade"]["level"] != Level.STORE_GREEN.value


def test_product_package_refuses_thin_store_green_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    client = TestClient(app)

    create_session("sess_thin_floor", "tester")
    out = tmp_path / "vetcare-hub-veterinary"
    _full_repo(out)
    _succeeded_pilot(out, product_id="vetcare-hub-veterinary")
    _write_provenance(out, VETCARE_THIN)
    state = get_session("sess_thin_floor")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "vetcare-hub-veterinary",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_thin_floor", state)

    pkg = client.get("/v1/sessions/sess_thin_floor/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in detail
    assert "full-pilot" in detail


def test_product_package_allows_steward_shaped_five(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    client = TestClient(app)

    create_session("sess_steward_floor", "tester")
    out = tmp_path / "cerebrum-steward"
    _full_repo(out)
    _succeeded_pilot(out, product_id="cerebrum-steward")
    _write_provenance(out, STEWARD_FIVE)
    state = get_session("sess_steward_floor")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "cerebrum-steward",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_steward_floor", state)

    pkg = client.get("/v1/sessions/sess_steward_floor/product/package")
    assert pkg.status_code == 200, pkg.text
    assert pkg.headers["content-type"].startswith("application/zip")
