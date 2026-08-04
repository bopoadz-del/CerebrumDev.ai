"""Generated platforms must live on the persistent disk and be backed up.

New-shape tests for the PRR fix: in production the repo root is ephemeral
container filesystem — only $STORAGE_PATH is a mounted disk. Before this fix
every generation was wiped by the next deploy and the package endpoint
answered 404 ("generate again"), burning the customer's metered export quota.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from app.core import backup
from app.factory.paths import (
    factory_outputs_root,
    factory_repo_root,
    is_within_outputs_root,
    safe_output_dir,
)


def test_outputs_root_follows_storage_path(monkeypatch, tmp_path):
    monkeypatch.delenv("FACTORY_OUTPUTS_ROOT", raising=False)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    assert factory_outputs_root() == tmp_path / "factory_outputs"
    # The default generation target lands on the disk too.
    assert safe_output_dir(None, "widget") == tmp_path / "factory_outputs" / "widget"


def test_explicit_override_beats_storage_path(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "disk"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "explicit"))
    assert factory_outputs_root() == tmp_path / "explicit"


def test_local_dev_layout_unchanged(monkeypatch):
    monkeypatch.delenv("FACTORY_OUTPUTS_ROOT", raising=False)
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    assert factory_outputs_root() == factory_repo_root() / "factory_outputs"


def test_storage_root_itself_stays_undeletable(monkeypatch, tmp_path):
    """Outputs under STORAGE_PATH must not widen the rmtree containment.

    safe_output_dir feeds shutil.rmtree; the accounts DB lives directly in
    STORAGE_PATH, so the storage root itself must never validate as a target.
    """
    monkeypatch.delenv("FACTORY_OUTPUTS_ROOT", raising=False)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    assert not is_within_outputs_root(tmp_path)
    assert not is_within_outputs_root(tmp_path / "accounts.db")
    assert is_within_outputs_root(tmp_path / "factory_outputs" / "p1")


def test_backup_includes_factory_outputs(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    (storage / "factory_outputs" / "acme-platform").mkdir(parents=True)
    (storage / "factory_outputs" / "acme-platform" / "README.md").write_text(
        "deliverable", encoding="utf-8"
    )
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))

    result = backup.create_backup()

    assert result.ok, result.error
    assert "factory_outputs" in result.included
    with tarfile.open(result.archive, "r:gz") as tar:
        names = tar.getnames()
    assert any("acme-platform" in n for n in names)
    assert any(n.endswith("README.md") for n in names)
