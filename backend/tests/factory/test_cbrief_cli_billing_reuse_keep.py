"""C-BRIEF empty-gap REUSE keep-path after FACTORY_CODE_CLI billing miss.

Photographed Floor (2026-09-05, sess_d5789a91d53b4bae, VetCare Hub /
veterinary-care, tip 327b4ae): inventory_gaps=[], all five caps REUSE,
FACTORY_CODE_CLI exited 1 with Moonshot ``429 ... suspended due to
insufficient balance``. Receipt kept_handler_ids=[] and budget_inspect
hard-stopped at stub_rate≈0.833 / SCAFFOLD.

This is compiler + emit + harvest + inspect — not a VetCare route patch.
Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    KEEP_PATH_FACTORY_GROUNDED_REUSE,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_FAILED,
)
from app.factory.build.gates import GateContext
from app.factory.build.persist_accept import FACTORY_GROUNDED_PERSIST_SOURCE
from app.factory.build.product_gate import gate_round_trip
from app.factory.build.roles import RoleContext, run_writer
from app.factory.build.workflow_accept import FACTORY_GROUNDED_EVENT_BUS_SOURCE
from app.factory.build.workspace import RoleWorkspace
from tests.factory.test_coder_session import _require_cli, _usable_kimi_toml


class _Cap:
    def __init__(self, cid, block_ids, strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _VetCare:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "sess_d5789a91 photograph — empty-gap REUSE after CLI billing"


def _billing_cli(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo '429 Too Many Requests — this account has been suspended "
        "due to insufficient balance'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_writer_empty_gap_billing_fail_allows_product_round_trip(
    tmp_path, monkeypatch
):
    """Empty-gap REUSE + CLI billing fail still persists — PRODUCT path open."""
    script = _billing_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )

    plan = _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow", "database"]),
        _Cap("clinic_dashboard", ["dashboard"]),
        _Cap("pet_records_management", ["database"]),
    )
    store_ids = {"event_bus", "workflow", "database", "dashboard"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    assert all(not item.is_gap for item in compiled.inventory)

    out = tmp_path / "vetcare"
    ws = RoleWorkspace(BuildRole.WRITER, out)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_VetCare(),
            plan=plan,
            state={
                "resolved_blocks": tuple(store_ids),
                "vendored_blocks": tuple(store_ids),
            },
        )
    )
    assert result.ok, result.detail
    assert oneshot == []
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["inventory_gaps"] == []
    assert receipt["kept_handler_ids"]
    for cid, want in (
        ("appointment_scheduling", FACTORY_GROUNDED_EVENT_BUS_SOURCE),
        ("clinic_dashboard", FACTORY_GROUNDED_PERSIST_SOURCE),
        ("pet_records_management", FACTORY_GROUNDED_PERSIST_SOURCE),
    ):
        text = (out / "app" / "actions" / f"{cid}.py").read_text(encoding="utf-8")
        assert "_persist_record(" in text, cid
        assert want in text, (cid, want)
        assert "deterministic contract template" not in text
    ctx = GateContext(workspace=out, role=BuildRole.TESTER, cycle="pilot")
    trip = gate_round_trip(ctx)
    assert trip.ok, (trip.detail, trip.findings)
    assert "pilot_zip" not in (trip.detail or "").lower()


def test_writer_nonempty_gaps_billing_fail_stays_fail_closed(tmp_path, monkeypatch):
    """GENERATE gap + billing miss must not claim keep-path or fake PRODUCT."""
    script = _billing_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)

    plan = _Plan(_Cap("novel_clinic_ai", [], "GENERATE"))
    compiled = compile_brief(_VetCare(), plan, store_ids={"database"})
    assert any(item.is_gap for item in compiled.inventory)
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "gap")
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    # Billing is not a preflight blocker — WRITER continues with templates.
    # Keep-path must stay off; receipt must not claim factory_grounded_reuse.
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_VetCare(),
            plan=plan,
            state={"resolved_blocks": (), "vendored_blocks": ()},
        )
    )
    assert result.ok, result.detail
    receipt = json.loads(
        (tmp_path / "gap" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["keep_path"] is None
    assert "novel_clinic_ai" in receipt["inventory_gaps"]
    assert receipt["kept_handler_ids"] == []
    handler = (
        tmp_path / "gap" / "app" / "actions" / "novel_clinic_ai.py"
    ).read_text(encoding="utf-8")
    assert "deterministic contract template" in handler
