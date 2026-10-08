"""The writer never authors a Factory-owned file (owner rule, 2026-10-08).

Live: the cycle-2 fintech writer (sess_59090c3bd0964425,
build/plt_464389e32e544810 @ 22e6be03) invented
docs/provenance/provenance.json from the blueprint and blocks.lock.json; the
gap-filling converge kept it, so the Factory's own provenance never landed and
provenance_complete failed on unknown commits. Nothing named the overwrite.

Now: one declared list of Factory-owned paths; a writer pass that creates,
edits or deletes one has the Factory's version put back and is sent to rework
naming the path -- never a silent overwrite, never silently kept.
"""

from __future__ import annotations

import json

from app.factory.build import factory_owned


# -- the one list -------------------------------------------------------------------


def test_the_list_is_derived_from_the_stamps_plus_provenance_and_the_store_gate():
    from app.factory.build.builds_push import STORE_GATE_PATH
    from app.factory.build.stamp_registry import owned_paths

    paths = set(factory_owned.factory_owned_paths())
    assert set(owned_paths()) <= paths
    assert {factory_owned.PROVENANCE_REL, STORE_GATE_PATH} <= paths
    assert len(paths) == len(set(owned_paths()) | {factory_owned.PROVENANCE_REL, STORE_GATE_PATH})


def test_the_list_carries_the_handovers_named_paths():
    paths = set(factory_owned.factory_owned_paths())
    for rel in (
        "docs/provenance/provenance.json",
        "scripts/acceptance.py",
        "scripts/factory_checks.py",
        "conftest.py",
        ".github/workflows/store-gate.yml",
    ):
        assert rel in paths, rel


# -- detection ------------------------------------------------------------------------


def test_created_modified_and_deleted_are_each_named(tmp_path):
    paths = ["a.txt", "b.txt", "c.txt", "d.txt"]
    (tmp_path / "b.txt").write_text("factory", encoding="utf-8")
    (tmp_path / "c.txt").write_text("factory", encoding="utf-8")
    (tmp_path / "d.txt").write_text("same", encoding="utf-8")
    before = factory_owned.snapshot(tmp_path, paths)
    (tmp_path / "a.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "b.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "c.txt").unlink()
    touched = factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths))
    assert touched == [
        {"path": "a.txt", "change": "created"},
        {"path": "b.txt", "change": "modified"},
        {"path": "c.txt", "change": "deleted"},
    ]


def test_restore_puts_the_factorys_version_back(tmp_path):
    paths = ["a.txt", "b.txt", "c.txt"]
    (tmp_path / "b.txt").write_text("factory", encoding="utf-8")
    (tmp_path / "c.txt").write_text("factory c", encoding="utf-8")
    before = factory_owned.snapshot(tmp_path, paths)
    (tmp_path / "a.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "b.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "c.txt").unlink()
    touched = factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths))
    factory_owned.restore(tmp_path, before, touched)
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "factory"
    assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "factory c"
    assert factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths)) == []


def test_the_record_is_factory_internal_and_never_shipped():
    from app.factory.build.builds_push import FACTORY_INTERNAL_PATHS

    assert factory_owned.VIOLATIONS_REL in FACTORY_INTERNAL_PATHS


# -- the WRITER gate turns it into rework naming the path ------------------------------


def test_the_writer_gate_sends_a_touched_factory_file_to_rework_by_name(tmp_path):
    from app.factory.build.gates import GateContext, gate_writer_contract
    from app.factory.build.authority import BuildRole

    factory_owned.record(tmp_path, [{"path": factory_owned.PROVENANCE_REL, "change": "created"}])
    result = gate_writer_contract(GateContext(workspace=tmp_path, role=BuildRole.WRITER))
    assert result.ok is False
    assert result.reason == "writer_authored_factory_file"
    assert any(factory_owned.PROVENANCE_REL in f and "Factory-owned" in f for f in result.findings)


def test_a_clean_pass_record_does_not_trip_the_gate(tmp_path):
    from app.factory.build.gates import GateContext, gate_writer_contract
    from app.factory.build.authority import BuildRole

    factory_owned.record(tmp_path, [])
    result = gate_writer_contract(GateContext(workspace=tmp_path, role=BuildRole.WRITER))
    assert result.reason != "writer_authored_factory_file"


# -- the production (CodeWhale) path --------------------------------------------------


def _run_pass(tmp_path, monkeypatch, write):
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from tests.factory.test_codewhale_writer_dispatch import (
        _ctx,
        _plant_authored_handler,
        _receipt,
    )

    ctx = _ctx(tmp_path)
    dest = tmp_path / "build"
    _plant_authored_handler(dest)

    def worker(_prompt, root, **_kw):
        write(root)
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", worker)
    result = _run_writer_via_codewhale_worker(ctx)
    return ctx, dest, result


def _writer_invents_provenance(root):
    from pathlib import Path

    target = Path(root) / factory_owned.PROVENANCE_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"product_id": "product", "schema_version": "1.0.0", "sources": [], "bindings": []}),
        encoding="utf-8",
    )


def test_a_writer_that_invents_provenance_is_recorded_and_never_kept(tmp_path, monkeypatch):
    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, _writer_invents_provenance)
    touched = factory_owned.recorded(dest)
    assert {"path": factory_owned.PROVENANCE_REL, "change": "created"} in touched
    # The writer's invention is gone; whatever provenance the product carries
    # now is the Factory's own (converge writes it after the writer).
    prov = dest / factory_owned.PROVENANCE_REL
    if prov.exists():
        data = json.loads(prov.read_text(encoding="utf-8"))
        assert "bindings" not in data and data.get("schema") == "factory_provenance.v1"


def test_a_writer_that_edits_a_stamped_file_is_recorded_and_restored(tmp_path, monkeypatch):
    from pathlib import Path

    rel = "scripts/factory_checks.py"
    (tmp_path / "build" / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "build" / rel).write_text("# the factory's self-check\n", encoding="utf-8")

    def edit(root):
        (Path(root) / rel).write_text("# the writer's version\n", encoding="utf-8")

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, edit)
    assert {"path": rel, "change": "modified"} in factory_owned.recorded(dest)
    assert (dest / rel).read_text(encoding="utf-8") != "# the writer's version\n"


def test_a_clean_writer_pass_records_nothing(tmp_path, monkeypatch):
    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, lambda _root: None)
    assert factory_owned.recorded(dest) == []


# -- the prompt -----------------------------------------------------------------------------


def test_the_prompt_names_every_factory_owned_file_and_asks_for_none():
    from app.factory.build.writer_prompt import render_writer_prompt

    class _Bp:
        product_id = product_name = vertical = summary = "probe"

    prompt = render_writer_prompt(_Bp(), brief="x")
    head, _, owned_section = prompt.partition("FACTORY-OWNED FILES")
    assert owned_section, "the prompt has no Factory-owned list"
    for rel in factory_owned.factory_owned_paths():
        assert f"- {rel}" in owned_section, rel
    # The OUTPUT list no longer asks the writer for a Factory-owned file.
    output = head.partition("OUTPUT")[2]
    for rel in factory_owned.factory_owned_paths():
        assert f"- {rel}\n" not in output, rel
