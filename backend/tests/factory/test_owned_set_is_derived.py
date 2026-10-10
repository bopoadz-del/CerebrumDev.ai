"""The Factory-owned set is DERIVED, never listed (owner spec, cycle 9).

Every Factory renderer registers a file when it writes it
(owned_registry.register / write_owned); the owned set is that registry, the
stamp registry's OWNED targets and the Store gate's files. A new Factory file
is owned the moment the Factory writes it -- no list is touched -- and the
writer snapshot/compare and the status reader both read the same set.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.factory.build import factory_owned, owned_registry

NEW = "docs/a_factory_file_no_list_names.json"


def test_rendering_a_new_factory_file_makes_it_owned_without_touching_a_list(tmp_path):
    assert NEW not in factory_owned.factory_owned_paths(tmp_path)
    owned_registry.write_owned(tmp_path, NEW, json.dumps({"made_by": "factory"}))
    assert NEW in factory_owned.factory_owned_paths(tmp_path)
    # A fresh process reads the same set off the tree.
    owned_registry.forget(tmp_path)
    assert NEW in factory_owned.factory_owned_paths(tmp_path)


def test_the_writer_snapshot_catches_a_write_to_a_newly_registered_file(tmp_path):
    owned_registry.write_owned(tmp_path, NEW, "{}")
    before = factory_owned.snapshot(tmp_path)
    (tmp_path / NEW).write_text('{"made_by": "writer"}', encoding="utf-8")
    touched = factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths=list(before)))
    assert {"path": NEW, "change": "modified"} in touched
    factory_owned.restore(tmp_path, before, touched)
    assert json.loads((tmp_path / NEW).read_text(encoding="utf-8")) == {}


def test_the_record_of_owned_files_is_itself_owned_and_never_shipped(tmp_path):
    from app.factory.build.builds_push import FACTORY_INTERNAL_PATHS

    owned_registry.register(tmp_path, NEW)
    assert owned_registry.REGISTRY_REL in factory_owned.factory_owned_paths(tmp_path)
    assert owned_registry.REGISTRY_REL in FACTORY_INTERNAL_PATHS


def test_the_status_reader_reads_the_factorys_record_only_when_it_is_owned(tmp_path):
    from app.factory.build.build_provenance import BUILD_RECORD_REL, build_record_text
    from app.factory.build_jobs import _authorship

    record = json.loads(build_record_text(None, []))
    record["brief_dispatch"] = {"cli_authored_ids": ["x"]}
    (tmp_path / "docs").mkdir()
    (tmp_path / BUILD_RECORD_REL).write_text(json.dumps(record), encoding="utf-8")
    assert not (_authorship(tmp_path).get("authorship") or {}).get("cli_authored_ids")
    owned_registry.register(tmp_path, BUILD_RECORD_REL)
    assert _authorship(tmp_path)["authorship"]["cli_authored_ids"] == ["x"]


def _string_collections(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            strings = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if strings:
                yield strings


def test_no_module_keeps_a_literal_owned_file_list():
    """The ownership module derives; it names no owned file in a collection,
    and factory_owned_paths holds no path literal at all."""
    src = Path(factory_owned.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for strings in _string_collections(tree):
        assert not [s for s in strings if "/" in s or s.endswith((".py", ".json", ".txt", ".yml"))], strings
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "factory_owned_paths")
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    literals = [
        n.value for stmt in body for n in ast.walk(stmt)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and ("/" in n.value or Path(n.value).suffix)
    ]
    assert not literals, f"factory_owned_paths names a path: {literals}"
