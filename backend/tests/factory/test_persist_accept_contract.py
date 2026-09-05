"""C-BRIEF must ground PRODUCT one-record persist / re-read.

Photographed Floor (2026-09-05, platforms-poll5 / sess_108e101) after a
successful Kimi FACTORY_CODE_CLI WRITER:

    PRODUCT (one-record round-trip): 3 capability(ies) did not remember
    a record they were given.

    audit: POST raised OperationalError: no such table: audit
    dashboard: POST raised OperationalError: no such table: dashboard
    veterinary_care_core: POST raised OperationalError: no such table:
        veterinary_care_core

Those ids are the keyword-fallback architect roster. This module is the
compiler + emit + harness contract — not a VetCare product patch.
Export honesty stays refused when the pilot suite / PRODUCT is red.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import brief_fingerprint, compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.data_lifecycle import (
    render_revision_0001,
    render_store,
)
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    KEYWORD_FALLBACK_VETCARE_CAPS,
    PERSIST_ISOLATE_NEEDLE,
    PRODUCT_NO_SUCH_TABLE_HALT,
    PRODUCT_ROUND_TRIP_CHECK,
    PRODUCT_ROUND_TRIP_HALT,
    WRITER_PERSIST_HALT,
    PersistRoundTripHalt,
    assert_persist_round_trip_ready,
    persist_accept_acceptance_line,
    persist_accept_brief_contract,
    persist_accept_forbidden_lines,
    persist_accept_needles,
    persist_accept_rules_text,
    persist_entity_of,
    persist_round_trip_errors,
    wipe_workspace_runtime_db,
)
from app.factory.build.product_gate import ROUND_TRIP_PROBE, gate_round_trip
from app.factory.build.roles_handlers import (
    _capability_handler_body,
    _fallback_spec,
    _handler_module,
    _templated_route_body,
)
from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import _WHOLE_JOB_SYSTEM
from app.factory.product_architect import (
    draft_blueprint_from_brief,
    lettings_golden_path,
    plan_blueprint,
)


ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"
LIVE_LETTINGS_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


class _Cap:
    def __init__(self, cid, block_ids=(), strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _VetCare:
    product_name = "Veterinary Care Platform"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def _keyword_fallback_plan():
    return _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
        _Cap("dashboard", ["dashboard"], "REUSE"),
    )


def test_photographed_caps_are_the_keyword_fallback_roster():
    bp = draft_blueprint_from_brief(
        "Build a veterinary care platform with a dashboard for the team",
        use_llm=False,
        use_golden_lettings=False,
        use_golden_steward=False,
    )
    assert bp.drafting_mode == "keyword_fallback"
    assert bp.product_id == "veterinary-care"
    ids = [c.id for c in bp.capabilities]
    for cid in KEYWORD_FALLBACK_VETCARE_CAPS:
        assert cid in ids, (cid, ids)
    assert persist_entity_of({}, "veterinary_care_core") == "veterinary_care_core"
    assert persist_entity_of({"entity": "audit"}, "audit") == "audit"


def test_vetcare_compiled_brief_grounds_persist_round_trip():
    compiled = compile_brief(
        _VetCare(),
        _keyword_fallback_plan(),
        store_ids={"audit", "dashboard", "database"},
    )
    text = compiled.text
    assert persist_accept_acceptance_line() in text
    assert persist_accept_forbidden_lines() in text
    assert persist_accept_brief_contract() in CODING_AGENT_BRIEF
    for needle in persist_accept_needles():
        assert needle.lower() in text.lower(), needle
    rules = persist_accept_rules_text()
    assert FACTORY_GROUNDED_PERSIST_SOURCE in rules
    assert PRODUCT_NO_SUCH_TABLE_HALT in rules
    assert "veterinary_care_core" in rules
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_lettings_and_smoke_still_lint_with_persist_accept():
    for path in (SMOKE, LETTINGS):
        bp = load_blueprint(path)
        compiled = compile_brief(bp, plan_blueprint(bp))
        assert PRODUCT_ROUND_TRIP_HALT in compiled.text
        assert persist_accept_acceptance_line() in compiled.text
        result = lint_brief(compiled)
        assert result.ok, (path.name, result.errors)


def test_lettings_golden_roster_and_fingerprint_unchanged():
    from app.factory.product_architect import draft_blueprint_from_brief as draft

    bp = draft(
        "build a platform for residential lettings",
        use_llm=False,
    )
    assert {c.id for c in bp.capabilities} == LIVE_LETTINGS_CAPS
    golden = load_blueprint(lettings_golden_path())
    assert {c.id for c in golden.capabilities} == LIVE_LETTINGS_CAPS
    compiled = compile_brief(bp, plan_blueprint(bp))
    fp = brief_fingerprint(compiled)
    assert set(fp["capabilities"]) == LIVE_LETTINGS_CAPS
    assert fp["missing_reuse"] == []
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_system_brief_and_oneshot_name_the_persist_halt():
    contract = persist_accept_brief_contract()
    assert PRODUCT_ROUND_TRIP_HALT in contract or "store.save(ENTITY, payload)" in contract
    assert PRODUCT_NO_SUCH_TABLE_HALT in contract
    assert contract in CODING_AGENT_BRIEF
    assert "store.save(ENTITY, payload)" in _WHOLE_JOB_SYSTEM
    assert PRODUCT_NO_SUCH_TABLE_HALT in _WHOLE_JOB_SYSTEM
    assert f"[check:{PRODUCT_ROUND_TRIP_CHECK}]" in persist_accept_acceptance_line()


def test_product_probe_and_writer_probe_both_isolate_storage():
    assert PERSIST_ISOLATE_NEEDLE in ROUND_TRIP_PROBE
    assert 'tempfile.mkdtemp(prefix="writer-gate-")' in BEHAVIOUR_PROBE
    assert "persist entity missing from migrated schema" in BEHAVIOUR_PROBE
    assert "no such table" in BEHAVIOUR_PROBE


def test_factory_generate_body_persists_instead_of_no_block_bound():
    body = _capability_handler_body("veterinary_care_core", [])
    assert "no_block_bound" not in body
    assert "_persist_record(payload)" in body
    module = _handler_module(
        "veterinary_care_core", [], body, "deterministic contract template",
        entity="veterinary_care_core",
    )
    assert "def _persist_record" in module
    assert "_store.save(ENTITY, record)" in module
    assert "from app import store as _store" in module


def test_factory_reuse_body_persists_after_blocks():
    body = _capability_handler_body("audit", ["audit"])
    assert "_persist_record(payload)" in body
    assert "no_block_bound" not in body


def test_templated_route_wraps_save_so_post_does_not_raise():
    spec = _fallback_spec(_Cap("audit", ["audit"]))
    body = _templated_route_body(spec)
    assert "stored = save(payload)" in body
    assert "except Exception as exc" in body


def _write_persist_workspace(tmp_path: Path, specs: dict, *, persist_handlers=True):
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "app" / "actions").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "0001_baseline.py").write_text(
        render_revision_0001(specs), encoding="utf-8"
    )
    (tmp_path / "app" / "store.py").write_text(render_store(specs), encoding="utf-8")
    routes = []
    for cid, spec in specs.items():
        name = cid.replace("-", "_")
        entity = persist_entity_of(spec, cid)
        if persist_handlers:
            body = _capability_handler_body(cid, list(spec.get("block_ids") or []))
        else:
            body = '    return {"ok": True, "capability": CAPABILITY_ID}'
        (tmp_path / "app" / "actions" / f"{name}.py").write_text(
            _handler_module(cid, spec.get("block_ids") or [], body, "test", entity=entity),
            encoding="utf-8",
        )
        routes.append(
            f'    save = lambda record: store.save("{entity}", record)\n'
            f"    stored = save(payload)\n"
        )
    (tmp_path / "app" / "routes.py").write_text(
        "from app import store\n" + "".join(routes), encoding="utf-8"
    )


def test_harness_fails_when_alembic_omits_a_persist_entity(tmp_path):
    specs = {
        "audit": {"entity": "audit", "fields": [{"name": "reference", "type": "str"}]},
        "dashboard": {
            "entity": "dashboard",
            "fields": [{"name": "reference", "type": "str"}],
        },
        "veterinary_care_core": {
            "entity": "veterinary_care_core",
            "fields": [{"name": "reference", "type": "str"}],
        },
    }
    dropped = dict(specs)
    dropped.pop("audit")
    _write_persist_workspace(tmp_path, dropped)
    # Routes/handlers still declare audit, but 0001 does not.
    (tmp_path / "app" / "actions" / "audit.py").write_text(
        _handler_module(
            "audit", ["audit"],
            _capability_handler_body("audit", ["audit"]),
            "test",
            entity="audit",
        ),
        encoding="utf-8",
    )
    specs_all = dict(specs)
    errors = persist_round_trip_errors(tmp_path, specs_all)
    assert any("audit" in e for e in errors)
    with pytest.raises(PersistRoundTripHalt) as halted:
        assert_persist_round_trip_ready(tmp_path, specs_all)
    assert WRITER_PERSIST_HALT in str(halted.value)
    assert "audit" in str(halted.value)


def test_harness_fails_when_handler_persists_to_the_wrong_table(tmp_path):
    specs = {
        "audit": {"entity": "audit_event", "fields": [{"name": "reference", "type": "str"}]},
    }
    _write_persist_workspace(tmp_path, specs)
    (tmp_path / "app" / "actions" / "audit.py").write_text(
        "def handle(payload):\n"
        "    from app import store\n"
        "    return store.save('audit', payload)\n",
        encoding="utf-8",
    )
    errors = persist_round_trip_errors(tmp_path, specs)
    assert any("audit" in e and "audit_event" in e for e in errors)


def test_harness_passes_factory_grounded_keyword_fallback(tmp_path):
    specs = {
        cid: _fallback_spec(_Cap(cid, [] if cid.endswith("_core") else [cid]))
        for cid in KEYWORD_FALLBACK_VETCARE_CAPS
    }
    _write_persist_workspace(tmp_path, specs)
    assert persist_round_trip_errors(tmp_path, specs) == []
    assert_persist_round_trip_ready(tmp_path, specs)


def test_wipe_workspace_runtime_db_removes_stale_platform_db(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "platform.db").write_bytes(b"stale")
    wipe_workspace_runtime_db(tmp_path)
    assert not data.exists()


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    monkeypatch.setenv("FACTORY_CODE_CLI", "")


def test_emitted_keyword_fallback_vetcare_round_trips(tmp_path):
    """Fail-closed: WRITER emit of the photographed roster one-record persists.

    Mutation killed: WRITER green / PRODUCT red because leftover ./data
    had 0001_baseline stamped without audit / dashboard /
    veterinary_care_core tables.

    Invokes run_writer (not RoleRunner): CI has no CEREBRUM_BLOCKS_ROOT,
    so CLONER cannot vendor Store shims. Persist + PRODUCT STORAGE_PATH
    isolation is the contract.
    """
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateContext
    from app.factory.build.roles import RoleContext, run_writer
    from app.factory.build.workspace import RoleWorkspace

    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path / "vetcare"
    generate_plan = _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", [], "GENERATE"),
        _Cap("dashboard", [], "GENERATE"),
    )
    ws = RoleWorkspace(BuildRole.WRITER, out)
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_VetCare(),
            plan=generate_plan,
            state={
                "resolved_blocks": (),
                "vendored_blocks": (),
                "gaps": list(KEYWORD_FALLBACK_VETCARE_CAPS),
            },
        )
    )
    assert result.ok, result.detail
    for cid in KEYWORD_FALLBACK_VETCARE_CAPS:
        path = out / "app" / "actions" / f"{cid}.py"
        assert path.is_file(), cid
        handler = path.read_text(encoding="utf-8")
        assert "_persist_record(" in handler or "store.save(" in handler
    revision = (out / "alembic" / "versions" / "0001_baseline.py").read_text(
        encoding="utf-8"
    )
    for entity in KEYWORD_FALLBACK_VETCARE_CAPS:
        assert f'"{entity}"' in revision
    stale = out / "data"
    stale.mkdir(exist_ok=True)
    (stale / "platform.db").write_bytes(b"not a migrated schema")
    ctx = GateContext(workspace=out, role=BuildRole.TESTER, cycle="pilot")
    trip = gate_round_trip(ctx)
    assert trip.ok, (trip.detail, trip.findings)
    assert "round-tripped" in (trip.detail or "")
    # Honesty: a red PRODUCT still refuses export. This test is a green
    # persist contract, not a claim that the live Floor shipped a zip.
    assert "pilot_zip" not in (trip.detail or "").lower()
