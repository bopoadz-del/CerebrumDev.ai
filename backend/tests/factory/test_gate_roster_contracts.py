"""The three gates added to close U1's siblings: integrity, UI surface, durability.

Each is checked both ways. A gate that only ever fails is not a gate, and a
gate that only ever passes is the hole it was written to close.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext
from app.factory.build.pilot_durability import (
    GATE_NAME as DURABILITY_GATE,
    gate_pilot_outcome_survives_restart,
)
from app.factory.build.ui_surface import (
    GATE_NAME as UI_GATE,
    MIN_MODULE_CHARS,
    declared_ui_modules,
    gate_ui_surface,
)
from app.factory.build.vendored_integrity import (
    GATE_NAME as INTEGRITY_GATE,
    LOCK_KEY,
    gate_vendored_integrity,
    lock_record,
    sha256_file,
)


def _runner(argv, *, cwd, timeout):
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def _ctx(workspace: Path, role: BuildRole, **kw) -> GateContext:
    return GateContext(workspace=workspace, role=role, runner=_runner, **kw)


# -- vendored integrity ---------------------------------------------------


def _block(root: Path, name: str, body: str, *, digest_for_body: str | None = None):
    """A vendored block plus the clone-time integrity record the gate reads.

    The record is computed from the SOURCE, mirroring run_cloner: the gate
    checks the recorded verdict, never the vendored bytes, because the
    CLONER rewrites imports on the way in.
    """
    src = root / "_source" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "block.py").write_text(body, encoding="utf-8")
    digest = digest_for_body or sha256_file(src / "block.py")
    (src / "block.json").write_text(
        json.dumps({"id": name, "digests": {"block.py": digest}}), encoding="utf-8"
    )

    d = root / "vendor" / "blocks" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "block.py").write_text(body, encoding="utf-8")
    (d / "block.json").write_text((src / "block.json").read_text(encoding="utf-8"), encoding="utf-8")

    lock = root / "blocks.lock.json"
    data = json.loads(lock.read_text(encoding="utf-8")) if lock.is_file() else {"blocks": {}}
    data.setdefault("blocks", {})[name] = {LOCK_KEY: lock_record(src)}
    lock.write_text(json.dumps(data), encoding="utf-8")
    return src


def test_integrity_gate_passes_when_bytes_match_the_manifest(tmp_path):
    _block(tmp_path, "alpha", "def run(**kw):\n    return {}\n")
    result = gate_vendored_integrity(_ctx(tmp_path, BuildRole.CLONER))
    assert result.ok is True, result.findings
    assert result.payload["files_hashed"] == 1


def test_integrity_gate_fails_when_source_did_not_match_its_manifest(tmp_path):
    # A stale mirror or a tampered source: the digest describes other bytes.
    _block(
        tmp_path,
        "alpha",
        "def run(**kw):\n    return {'evil': True}\n",
        digest_for_body="0" * 64,
    )
    result = gate_vendored_integrity(_ctx(tmp_path, BuildRole.CLONER))
    assert result.ok is False
    assert result.gate == INTEGRITY_GATE
    assert any("hash to" in f for f in result.findings), result.findings


def test_integrity_gate_fails_a_block_vendored_with_no_record(tmp_path):
    """Skipping verification must fail exactly like failing it."""
    d = tmp_path / "vendor" / "blocks" / "ghost"
    d.mkdir(parents=True)
    (d / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    (tmp_path / "blocks.lock.json").write_text(
        json.dumps({"blocks": {}}), encoding="utf-8"
    )

    result = gate_vendored_integrity(_ctx(tmp_path, BuildRole.CLONER))
    assert result.ok is False
    assert any(
        "no clone-time integrity record" in f for f in result.findings
    ), result.findings


def test_integrity_gate_reports_rather_than_fails_a_block_with_no_digests(tmp_path):
    src = tmp_path / "_source" / "legacy"
    src.mkdir(parents=True)
    (src / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    (src / "block.json").write_text(json.dumps({"id": "legacy"}), encoding="utf-8")

    d = tmp_path / "vendor" / "blocks" / "legacy"
    d.mkdir(parents=True)
    (d / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    (tmp_path / "blocks.lock.json").write_text(
        json.dumps({"blocks": {"legacy": {LOCK_KEY: lock_record(src)}}}),
        encoding="utf-8",
    )

    result = gate_vendored_integrity(_ctx(tmp_path, BuildRole.CLONER))
    assert result.ok is True
    assert result.payload["blocks_without_digests"] == ["legacy"]


# -- UI surface -----------------------------------------------------------


def _declare_ui(root: Path, modules):
    p = root / "docs" / "blueprint"
    p.mkdir(parents=True, exist_ok=True)
    (p / "product_blueprint.json").write_text(
        json.dumps({"ui_modules": modules}), encoding="utf-8"
    )


def test_ui_gate_passes_when_nothing_is_declared(tmp_path):
    result = gate_ui_surface(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is True
    assert "nothing claimed" in result.detail


def test_ui_gate_fails_when_a_declared_module_was_never_emitted(tmp_path):
    _declare_ui(tmp_path, ["command_center"])
    result = gate_ui_surface(_ctx(tmp_path, BuildRole.WRITER))

    assert result.ok is False
    assert result.gate == UI_GATE
    assert any("command_center" in f for f in result.findings), result.findings


def test_ui_gate_fails_on_a_placeholder_module(tmp_path):
    _declare_ui(tmp_path, ["command_center"])
    mod = tmp_path / "frontend" / "src" / "modules"
    mod.mkdir(parents=True)
    (mod / "command_center.tsx").write_text("// TODO\n", encoding="utf-8")

    result = gate_ui_surface(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False
    assert any("placeholder" in f for f in result.findings), result.findings


def test_ui_gate_passes_on_a_real_module(tmp_path):
    _declare_ui(tmp_path, ["command_center"])
    mod = tmp_path / "frontend" / "src" / "modules"
    mod.mkdir(parents=True)
    (mod / "command_center.tsx").write_text("x" * (MIN_MODULE_CHARS + 1), encoding="utf-8")

    result = gate_ui_surface(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is True, result.findings


def test_declared_ui_modules_reads_the_emitted_blueprint(tmp_path):
    _declare_ui(tmp_path, ["command_center", "ops"])
    assert declared_ui_modules(tmp_path) == ["command_center", "ops"]


# -- pilot durability -----------------------------------------------------


def test_durability_gate_is_a_noop_on_the_code_cycle(tmp_path):
    result = gate_pilot_outcome_survives_restart(
        _ctx(tmp_path, BuildRole.STORE_MANAGER, cycle="code")
    )
    assert result.ok is True
    assert "code cycle" in result.detail


def test_durability_gate_fails_a_pilot_workspace_with_no_models(tmp_path):
    result = gate_pilot_outcome_survives_restart(
        _ctx(tmp_path, BuildRole.STORE_MANAGER, cycle="pilot")
    )
    assert result.ok is False
    assert result.gate == DURABILITY_GATE
