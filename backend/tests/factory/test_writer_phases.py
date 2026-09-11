"""One FACTORY_CODE_CLI WRITER, three fail-closed C-BRIEF phases.

CHADi 2026-09-12: backend → frontend+RAG → integration. Resume uses the
#403 checkpoint spine. Phase N must accept before phase N+1.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.persist_accept import persist_handler_rel
from app.factory.build.runner import (
    RoleRunner,
    blueprint_hash,
)
from app.factory.build.writer_phases import (
    CHECKPOINT_STAGE,
    WRITER_PHASE_BACKEND,
    WRITER_PHASE_FRONTEND_RAG,
    WRITER_PHASE_INTEGRATION,
    WRITER_PHASES,
    PhaseAcceptHalt,
    accept_writer_phase,
    checkpoint_landed_phase,
    compile_phase_brief,
    inventory_needs_rag,
    landed_phase_ids,
    pending_writer_phases,
    phase_acceptance_errors,
    prior_writer_phase,
    should_dispatch_writer_phase,
    should_reopen_writer_phase,
    writer_phase_needles,
)
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


class _Cap:
    def __init__(self, cid, block_ids=(), strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def _compiled_smoke():
    bp = load_blueprint(SMOKE)
    return compile_brief(bp, plan_blueprint(bp))


def _ctx(tmp_path, compiled, landed=()):
    root = tmp_path / "ws"
    root.mkdir()
    notes = []
    ctx = SimpleNamespace(
        workspace=SimpleNamespace(workspace=root),
        state={
            "inputs_hash": "digest",
            "landed_writer_phases": list(landed),
        },
        note=lambda detail, **payload: notes.append((detail, payload)),
    )
    ctx._notes = notes
    ctx._compiled = compiled
    return ctx


def _plant_backend(root: Path, compiled) -> None:
    (root / "app" / "actions").mkdir(parents=True, exist_ok=True)
    (root / "app" / "models.py").write_text("MODELS = {}\n", encoding="utf-8")
    routes = []
    for cid in compiled.capabilities:
        name = cid.replace("-", "_")
        (root / persist_handler_rel(cid)).write_text(
            f"CAPABILITY = {cid!r}\n", encoding="utf-8"
        )
        routes.append(f'@app.post("/v1/{name}")\nasync def create_{name}():\n    return {{}}\n')
        routes.append(f'@app.get("/v1/{name}")\nasync def list_{name}():\n    return {{}}\n')
    (root / "app" / "routes.py").write_text("\n".join(routes), encoding="utf-8")


def _plant_render_ready(root: Path) -> None:
    (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (root / "render.yaml").write_text("services: []\n", encoding="utf-8")
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text("app = None\n", encoding="utf-8")


def test_compiled_brief_carries_three_phase_cuts_and_lints():
    compiled = _compiled_smoke()
    text = compiled.text
    for needle in writer_phase_needles():
        assert needle in text, needle
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert "CUT 1" in text and "CUT 3" in text


def test_compile_phase_brief_does_not_drop_horizon_or_reuse_accept():
    compiled = _compiled_smoke()
    phase = compile_phase_brief(compiled, WRITER_PHASE_BACKEND)
    assert "PHASE 1 of 3 ACTIVE" in phase.text
    assert "Do only this phase" in phase.text
    assert compiled.text in phase.text
    assert "BLOCK_DEFAULT_ACTIONS" in phase.text
    assert lint_brief(phase).ok, lint_brief(phase).errors


def test_phase_order_is_backend_then_ui_rag_then_integration():
    assert WRITER_PHASES == (
        WRITER_PHASE_BACKEND,
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    )
    assert prior_writer_phase(WRITER_PHASE_BACKEND) is None
    assert prior_writer_phase(WRITER_PHASE_FRONTEND_RAG) == WRITER_PHASE_BACKEND
    assert prior_writer_phase(WRITER_PHASE_INTEGRATION) == WRITER_PHASE_FRONTEND_RAG


def test_fail_closed_phase_two_before_phase_one_accepted(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_gate"):
        accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)


def test_fail_closed_phase_three_before_phase_two_accepted(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled, landed=[WRITER_PHASE_BACKEND])
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_gate"):
        accept_writer_phase(ctx, WRITER_PHASE_INTEGRATION, compiled)


def test_phase_one_accept_requires_handler_route_schema(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_backend"):
        accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    assert WRITER_PHASE_BACKEND in ctx.state["landed_writer_phases"]


def test_rework_reopens_backend_even_when_phase_landed(tmp_path):
    """TESTER findings must reach the coder; resume skip does not apply."""
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    ctx.work_list = ()
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND) is False
    ctx.work_list = ["tester produced no test files"]
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND) is True
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG) is False


def test_resume_skips_landed_earlier_phases(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    assert pending_writer_phases(ctx) == [
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    ]
    assert WRITER_PHASE_BACKEND not in pending_writer_phases(ctx)


def test_landed_phase_ids_read_the_403_checkpoint_spine(tmp_path):
    bp = load_blueprint(SMOKE)
    digest = blueprint_hash(bp)
    other = blueprint_hash(
        load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    )
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="runner-smoke", inputs_hash=digest)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed writer phase backend",
        payload={
            "stage": CHECKPOINT_STAGE,
            "phase": WRITER_PHASE_BACKEND,
            "inputs_hash": digest,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed capability analytics_surface",
        payload={
            "stage": CHECKPOINT_STAGE,
            "capability": "analytics_surface",
            "inputs_hash": digest,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed writer phase from another blueprint",
        payload={
            "stage": CHECKPOINT_STAGE,
            "phase": WRITER_PHASE_BACKEND,
            "inputs_hash": other,
        },
    )
    assert landed_phase_ids(ledger, digest) == [WRITER_PHASE_BACKEND]
    assert "frontend_rag" not in landed_phase_ids(ledger, digest)


def test_later_phase_dispatch_only_on_cli_path():
    assert should_dispatch_writer_phase(WRITER_PHASE_BACKEND, None) is True
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG, SimpleNamespace(via="http_oneshot")
    ) is False
    assert should_dispatch_writer_phase(
        WRITER_PHASE_INTEGRATION, SimpleNamespace(via="skipped")
    ) is False
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG, SimpleNamespace(via="cli")
    ) is True


def test_phase_two_rag_required_when_inventory_names_rag_surface():
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("dual_rag_estate_docs", ["rag", "database"], "REUSE")),
        store_ids={"rag", "database"},
    )
    assert inventory_needs_rag(compiled)
    assert "RAG ingest/query" in compiled.text


def test_phase_two_rag_not_required_for_vector_search_reuse():
    """VetCare patient_records binds vector_search — reuse_accept, not RAG."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("patient_records_management", ["vector_search", "database"], "REUSE")),
        store_ids={"vector_search", "database"},
    )
    assert inventory_needs_rag(compiled) is False


def test_phase_three_accept_requires_render_ready_not_live_deploy(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(
        tmp_path,
        compiled,
        landed=[WRITER_PHASE_BACKEND, WRITER_PHASE_FRONTEND_RAG],
    )
    errors = phase_acceptance_errors(ctx, WRITER_PHASE_INTEGRATION, compiled)
    assert errors
    assert any("render-ready" in e for e in errors)
    _plant_render_ready(ctx.workspace.workspace)
    accept_writer_phase(ctx, WRITER_PHASE_INTEGRATION, compiled)


def test_runner_hydrates_landed_writer_phases(tmp_path):
    bp = load_blueprint(SMOKE)
    digest = blueprint_hash(bp)
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="runner-smoke", inputs_hash=digest)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed writer phase backend",
        payload={
            "stage": CHECKPOINT_STAGE,
            "phase": WRITER_PHASE_BACKEND,
            "inputs_hash": digest,
        },
    )
    runner = RoleRunner(bp, out)
    runner._restore_workspace_state()
    assert WRITER_PHASE_BACKEND in runner.state.get("landed_writer_phases", [])
    ctx = SimpleNamespace(state=runner.state)
    assert WRITER_PHASE_BACKEND not in pending_writer_phases(ctx)
