"""The three contract gates must be shown to say NO.

Their parts are tested elsewhere. The composites were not, and a composite
that forgets to propagate a failing part is exactly the defect the roster
change was made to prevent: gate_writer_contract exists because compilation
alone passed a route that discarded its handler's result.

scripts/audit_gates.py fails CI without these, so the absence cannot recur
quietly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.gates import (
    GateContext,
    gate_cloner_contract,
    gate_store_manager_contract,
    gate_writer_contract,
)


def _runner(argv, *, cwd, timeout):
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def _ctx(workspace: Path, role: BuildRole, **kw) -> GateContext:
    return GateContext(workspace=workspace, role=role, runner=_runner, **kw)


# -- STORE_MANAGER --------------------------------------------------------


def test_store_manager_contract_refuses_a_pilot_with_no_store_ops(tmp_path):
    """A pilot cycle that recorded no store op has not proven authorisation."""
    result = gate_store_manager_contract(
        _ctx(tmp_path, BuildRole.STORE_MANAGER, cycle="pilot", store_ops=())
    )
    assert result.ok is False
    assert "no store ops" in result.detail


def test_store_manager_contract_propagates_the_failing_part(tmp_path):
    """The composite must return the part's verdict, not its own summary."""
    result = gate_store_manager_contract(
        _ctx(tmp_path, BuildRole.STORE_MANAGER, cycle="pilot", store_ops=())
    )
    assert result.gate == "store_ops_authorised", (
        "the composite reported its own name for a failure raised by a part, "
        "which hides which check actually refused"
    )


# -- CLONER ---------------------------------------------------------------


def test_cloner_contract_refuses_a_block_that_is_not_vendored(tmp_path):
    """Import proves the clone runs. A named block that is absent cannot."""
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    result = gate_cloner_contract(
        _ctx(tmp_path, BuildRole.CLONER, vendored_blocks=("no_such_block",))
    )
    assert result.ok is False


# -- WRITER ---------------------------------------------------------------


def test_writer_contract_refuses_a_workspace_that_does_not_parse(tmp_path):
    """Syntax first, because its failure mode is the clearest."""
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "routes.py").write_text("def broken(:\n", encoding="utf-8")
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False


def test_writer_contract_does_not_stop_at_compilation(tmp_path):
    """The reason this gate replaced gate_workspace_compiles.

    A workspace that parses cleanly must still not pass on syntax alone --
    if it does, the behaviour and UI-surface halves are unreachable and the
    composite is gate_workspace_compiles under a new name.
    """
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "routes.py").write_text("x = 1\n", encoding="utf-8")
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False, (
        "a workspace containing nothing but a valid assignment passed the "
        "WRITER contract; only the compile half can be running"
    )
    assert result.gate != "workspace_compiles" or "compile" not in result.detail.lower()


def _write_provenance(root: Path, sources: dict, dispatch: dict | None = None) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "build_provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "build_provenance.v1",
                "artifact_sources": sources,
                "brief_dispatch": dispatch or {},
            }
        ),
        encoding="utf-8",
    )


def test_writer_contract_refuses_zero_agent_artifacts(tmp_path):
    """Provenance with agent_written=0 is writer_no_output, never a pass.

    A compiling tree of pure templates is still not a WRITER pass:
    templated file writes do not count as agent authorship.
    """
    _write_provenance(
        tmp_path,
        {
            "audit": "deterministic contract template",
            "workflow": "deterministic contract template",
        },
    )
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False
    assert result.gate == "writer_contract"
    assert "writer_no_output" in result.detail
    assert "templated file writes do not count" in result.detail
    assert result.findings and any(
        "writer_no_output" in f for f in result.findings
    )
    assert result.payload.get("agent_written") == 0
    assert result.payload.get("templated") == 2


def test_writer_contract_keep_path_ids_are_not_zero_output(tmp_path):
    """CLI keep-path cli_authored_ids are agent work, not zero output."""
    _write_provenance(
        tmp_path,
        {"readme": "deterministic contract template"},
        {
            "via": "cli",
            "ok": True,
            "cli_authored_ids": ["audit", "workflow"],
        },
    )
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert "writer_no_output" not in result.detail


def test_writer_contract_missing_provenance_is_not_zero_output(tmp_path):
    """No provenance file keeps the legacy path — the check only fires on
    measured zero, never on absence."""
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "routes.py").write_text("def broken(:\n", encoding="utf-8")
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False
    assert "writer_no_output" not in result.detail
