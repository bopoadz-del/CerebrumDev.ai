"""RoleWorkspace vs pathlib.Path workspace protocol (CEREBRUMDEV-BACKEND-W/V).

Sentry W: harvest treated Path as exists(rel) because Path.exists exists.
Sentry V: emit_writer_artifacts assumed write_text(rel, content); Floor
WRITER RoleWorkspace implements that, Path / incomplete RW do not.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.data_lifecycle import emit_writer_artifacts
from app.factory.build.reuse_accept import (
    harvest_block_default_action,
    harvest_block_default_actions,
)
from app.factory.build.workspace import (
    RoleWorkspace,
    supports_relpath_io,
    supports_relpath_write,
    write_workspace_text,
)


_PLANTED_ACTION = "planted_query"
_SPECS = {
    "customer_records": {
        "entity": "customer_records",
        "fields": [{"name": "name", "type": "str"}],
    }
}


def _plant_vendor_block_json(root: Path, block_id: str = "database") -> None:
    dest = root / "vendor" / "blocks" / block_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "block.json").write_text(
        json.dumps(
            {
                "id": block_id,
                "inputs": [
                    {
                        "name": "action",
                        "type": "string",
                        "default": _PLANTED_ACTION,
                        "options": [_PLANTED_ACTION],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _disable_factory_harvest(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.factory.build.reuse_accept.STORE_BLOCK_DEFAULT_ACTIONS",
        {},
    )
    monkeypatch.setattr(
        "app.factory.build.reuse_accept._harvest_from_factory_vendor",
        lambda _bid: None,
    )


def test_path_is_not_relpath_workspace_protocol(tmp_path):
    assert supports_relpath_io(tmp_path) is False
    assert supports_relpath_write(tmp_path) is False
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path)
    assert supports_relpath_io(ws) is True
    assert supports_relpath_write(ws) is True


def test_harvest_path_workspace_missing_vendor_does_not_typeerror(
    tmp_path, monkeypatch
):
    """CEREBRUMDEV-BACKEND-W photograph: Path workspace + database/_v2."""
    _disable_factory_harvest(monkeypatch)
    harvested = harvest_block_default_action("database", workspace=tmp_path)
    assert harvested is None
    assert harvest_block_default_actions(
        ["database", "database_v2"], workspace=tmp_path
    ) == {}


def test_harvest_path_workspace_reads_vendor_block_json(tmp_path, monkeypatch):
    """Path root must harvest vendor/blocks/<id>/block.json without TypeError."""
    _disable_factory_harvest(monkeypatch)
    _plant_vendor_block_json(tmp_path)
    assert harvest_block_default_action("database", workspace=tmp_path) == _PLANTED_ACTION
    assert harvest_block_default_actions(
        ["database"], tmp_path, workspace=tmp_path
    ) == {"database": _PLANTED_ACTION}


def test_harvest_role_workspace_reads_vendor_block_json(tmp_path, monkeypatch):
    """Floor WRITER RoleWorkspace still uses exists(rel)/read_text(rel)."""
    _disable_factory_harvest(monkeypatch)
    dest = tmp_path / "dest"
    dest.mkdir()
    _plant_vendor_block_json(dest)
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    assert harvest_block_default_action("database", workspace=ws) == _PLANTED_ACTION
    assert harvest_block_default_actions(["database"], workspace=ws) == {
        "database": _PLANTED_ACTION
    }


def test_emit_writer_artifacts_role_workspace(tmp_path):
    """Floor WRITER path: RoleWorkspace.write_text(rel, content)."""
    dest = tmp_path / "dest"
    dest.mkdir()
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    emit_writer_artifacts(ws, _SPECS)
    store = dest / "app" / "store.py"
    assert store.is_file()
    assert "customer_records" in store.read_text(encoding="utf-8")
    assert "app/store.py" in ws.written


def test_emit_writer_artifacts_path_root(tmp_path):
    """Path root must not call Path.write_text(rel, content)."""
    emit_writer_artifacts(tmp_path, _SPECS)
    store = tmp_path / "app" / "store.py"
    assert store.is_file()
    assert "customer_records" in store.read_text(encoding="utf-8")


def test_emit_writer_artifacts_rw_without_write_text(tmp_path):
    """CEREBRUMDEV-BACKEND-V: selfcheck RW missing write_text falls back."""

    class RW:
        def __init__(self, root: Path) -> None:
            self.workspace = root

    emit_writer_artifacts(RW(tmp_path), _SPECS)
    store = tmp_path / "app" / "store.py"
    assert store.is_file()
    assert "customer_records" in store.read_text(encoding="utf-8")


def test_write_workspace_text_path_and_role(tmp_path):
    rel = Path("app") / "note.txt"
    write_workspace_text(tmp_path, rel, "via-path\n")
    assert (tmp_path / rel).read_text(encoding="utf-8") == "via-path\n"
    dest = tmp_path / "role"
    dest.mkdir()
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    write_workspace_text(ws, rel, "via-role\n")
    assert (dest / rel).read_text(encoding="utf-8") == "via-role\n"
