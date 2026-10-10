"""docs/build_provenance.json is the Factory's record: rendered before the
writer, re-rendered after it from the handlers on disk, never the writer's
(FACTORY defect, release cycle 9).

Live: vineyard anchor (sess_a7c02f0cf81a4178, build/plt_7232365d00e34f53): the
writer wrote docs/build_provenance.json itself (keys no Factory emitter
writes), listing models and docs but no handler; the Factory "left an
agent-written manifest alone", and a 22/22 Store-green build with 8
agent-stamped handlers was graded action_py=0 and refused its package.

The record is now on the one Factory-owned list (data): the Factory renders it
before the pass, a writer that writes it is restored and re-prompted with the
path, and after the pass the Factory renders it from the stamped handlers.

Synthetic products only.
"""

from __future__ import annotations

import json

from app.factory.build import factory_owned

REL = "docs/build_provenance.json"


def test_the_build_record_is_owned_once_the_factory_renders_it(tmp_path):
    """Owned by derivation: its renderer registered it when it wrote it."""
    from app.factory.build.build_provenance import BUILD_RECORD_REL

    assert BUILD_RECORD_REL == REL
    assert REL not in factory_owned.factory_owned_paths(tmp_path)
    ctx = type("C", (), {"blueprint": None, "state": {}})()
    factory_owned.prestamp_late_files(tmp_path, ctx)
    assert REL in factory_owned.factory_owned_paths(tmp_path)


def test_the_build_record_is_rendered_before_the_writer(tmp_path):
    class _Bp:
        product_id = "p"
        product_name = "n"
        capabilities = ["a", "b", "c"]

    ctx = type("C", (), {"blueprint": _Bp(), "state": {}})()
    changed = factory_owned.prestamp_late_files(tmp_path, ctx)
    assert REL in changed
    record = json.loads((tmp_path / REL).read_text(encoding="utf-8"))
    assert record["written_by"].startswith("factory")
    assert record["n_required"] == 3


def _writer_writes_its_own_record(root):
    from pathlib import Path

    target = Path(root) / REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "schema": "writer-manifest",
            "roster_source": "self",
            "artifact_sources": {"app/models.py": "coder CLI (codewhale exec)"},
        }),
        encoding="utf-8",
    )


def test_a_writer_authored_build_record_is_rejected_and_authorship_comes_from_the_factorys(
    tmp_path, monkeypatch
):
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateContext, gate_writer_contract
    from app.factory.build_jobs import _authorship
    from tests.factory.test_writer_never_authors_factory_files import _run_pass

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, _writer_writes_its_own_record)

    touched = factory_owned.recorded(dest)
    assert any(row["path"] == REL for row in touched), touched
    verdict = gate_writer_contract(GateContext(workspace=dest, role=BuildRole.WRITER))
    assert verdict.reason == factory_owned.WRITER_AUTHORED and REL in verdict.detail

    record = json.loads((dest / REL).read_text(encoding="utf-8"))
    assert "roster_source" not in record, record
    assert record["written_by"].startswith("factory")
    # The Factory's record names the stamped handler the pass left on disk.
    assert _authorship(dest)["authorship"]["action_py"] == 1


def test_a_clean_pass_gets_the_factorys_record_from_the_handlers(tmp_path, monkeypatch):
    from app.factory.build_jobs import _authorship
    from tests.factory.test_writer_never_authors_factory_files import _run_pass

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, lambda _root: None)
    assert not [r for r in factory_owned.recorded(dest) if r["path"] == REL]
    record = json.loads((dest / REL).read_text(encoding="utf-8"))
    assert record["artifact_sources"] == {"app/actions/cap.py": "coder CLI (codewhale exec)"}
    assert _authorship(dest)["authorship"]["action_py"] == 1


def test_a_rerender_keeps_what_other_factory_steps_added(tmp_path):
    from app.factory.build.build_provenance import build_record_text, carried_record_fields

    old = tmp_path / REL
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"brief_dispatch": {"cli_authored_ids": ["x"]}, "roster_source": "w"}), encoding="utf-8")
    record = json.loads(build_record_text(None, ["x"], carried=carried_record_fields(old)))
    assert record["brief_dispatch"] == {"cli_authored_ids": ["x"]}
    assert "roster_source" not in record
