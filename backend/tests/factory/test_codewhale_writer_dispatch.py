"""Phase 5 dispatch wiring: the CodeWhale worker as the WRITER target."""

from __future__ import annotations

import pytest

from app.factory.build.codewhale_worker import WorkerError
from app.factory.build.roles_handlers import (
    _run_writer_via_codewhale_worker,
    writer_uses_codewhale,
)
from app.factory.build.roles_models import RoleError
from app.factory.build.writer_prompt import render_writer_prompt


def test_writer_uses_codewhale_is_opt_in():
    assert writer_uses_codewhale({}) is False
    assert writer_uses_codewhale({"FACTORY_CODEWHALE_WRITER": "0"}) is False
    assert writer_uses_codewhale({"FACTORY_CODEWHALE_WRITER": "1"}) is True


def test_prompt_instructs_the_author_stamp():
    text = render_writer_prompt(
        type("B", (), {"product_id": "p", "product_name": "n", "vertical": "v", "summary": "s"})(),
        brief="probe",
    )
    assert "Written by the factory WRITER role (codewhale exec)" in text


def _ctx(tmp_path):
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=type(
            "B",
            (),
            {"product_id": "p", "product_name": "n", "vertical": "v", "summary": "s"},
        )(),
        plan=None,
        state={},
    )


def _receipt(tools, **kw):
    base = {
        "status": "completed",
        "termination_reason": "resolved",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "output": "built",
        "tools": tools,
        "error": None,
        "error_category": None,
    }
    base.update(kw)
    base["to_dict"] = lambda self: {
        k: v
        for k, v in type(self).__dict__.items()
        if k != "to_dict" and not k.startswith("__")
    }
    return type("R", (), base)()


def _plant_authored_handler(root):
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / "cap.py").write_text(
        '"""Handler for capability cap.\n\n'
        'Written by the factory WRITER role (codewhale exec).\n'
        '"""\n',
        encoding="utf-8",
    )


def test_worker_dispatch_records_the_receipt(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")

    def fake_run(prompt, dest, tenant_store=None, session_id=""):
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(ctx)
    assert result.ok is True
    assert result.notes["codewhale_worker"]["status"] == "completed"
    # E2: the Floor monitor receipt reflects the leg that ran.
    receipt_file = tmp_path / "build" / "docs" / "coder_receipt.json"
    assert receipt_file.is_file()
    import json as _json
    payload = _json.loads(receipt_file.read_text(encoding="utf-8"))
    assert payload["via"] == "codewhale_worker"


def test_worker_succeeded_but_wrote_nothing_is_refused(tmp_path, monkeypatch):
    """E1: a 'completed' receipt with zero tool calls or zero stamped
    handlers is the same silent success the writer_contract gate refuses —
    the role must refuse it, not report ok=True."""
    def fake_run(prompt, dest, tenant_store=None, session_id=""):
        return _receipt(tools=[])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert result.ok is False
    assert "writer_no_output" in result.detail


def test_worker_succeeded_with_tools_but_no_stamped_handlers_is_refused(
    tmp_path, monkeypatch
):
    """E1: tool calls alone are not authorship — the disk-level stamp
    count is what counts."""
    def fake_run(prompt, dest, tenant_store=None, session_id=""):
        return _receipt(tools=[{"tool": "write", "path": "notes.txt"}])

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", fake_run
    )
    result = _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert result.ok is False
    assert "writer_no_output" in result.detail


def test_worker_failure_raises_a_named_role_error(tmp_path, monkeypatch):
    def boom(prompt, dest, tenant_store=None, session_id=""):
        raise WorkerError("worker_exec_failed: nope")

    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", boom
    )
    with pytest.raises(RoleError) as exc:
        _run_writer_via_codewhale_worker(_ctx(tmp_path))
    assert "codewhale_worker_failed" in str(exc.value)


def test_codewhale_stamp_counts_as_agent_output(tmp_path):
    """The worker's stamped handlers satisfy the Phase 0.5 artifact gate."""
    from app.factory.build.authorship import agent_written_handler_ids_in_workspace

    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "cap.py").write_text(
        '"""Handler for capability cap.\n\n'
        "Written by the factory WRITER role (codewhale exec).\n"
        '"""\n',
        encoding="utf-8",
    )
    assert agent_written_handler_ids_in_workspace(tmp_path) == ["cap"]
