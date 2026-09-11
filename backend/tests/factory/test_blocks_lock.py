"""Factory store pin: blocks.lock.json is the only resolution path.

A recorded hash that does not match the store tree is a HARD FAILURE.
The message names the block and both hashes. There is no warn path and
no fall-through to latest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.blocks_lock import (
    BlocksLockError,
    assert_block_matches_lock,
    block_content_hash,
    generate_lock,
)
from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext, RoleError, run_cloner
from app.factory.build.workspace import RoleWorkspace
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
BASIC = ROOT / "blueprints/examples/basic_product.yaml"


def _store_with(root: Path, block_id: str, body: str = "VALUE = 1\n") -> Path:
    store = root / "store"
    d = store / "block_registry" / block_id
    d.mkdir(parents=True)
    (d / "block.py").write_text(body, encoding="utf-8")
    (d / "block.json").write_text(
        json.dumps({"id": block_id, "version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    return store


def _clone(tmp_path: Path, store: Path, block_ids, lock):
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store,
        blocks_lock=lock,
        state={"resolved_blocks": tuple(block_ids)},
    )
    return ws, run_cloner(ctx)


def test_recorded_hash_mismatch_fails_the_build_naming_both_hashes(tmp_path):
    """The failing-first contract: a fixture lock whose hash does not match
    must abort the build and name the block plus both hashes."""
    store = _store_with(tmp_path, "analytics", "VALUE = 1\n")
    lock = generate_lock(store, consumed_ids=["analytics"])
    actual = lock["blocks"]["analytics"]["content_hash"]
    recorded = "sha256:" + ("0" * 64)
    lock["blocks"]["analytics"]["content_hash"] = recorded
    assert actual != recorded

    with pytest.raises((BlocksLockError, RoleError)) as exc:
        _clone(tmp_path, store, ("analytics",), lock)

    msg = str(exc.value)
    assert "analytics" in msg, msg
    assert recorded in msg, msg
    assert actual in msg, msg


def test_assert_helper_names_block_and_both_hashes(tmp_path):
    store = _store_with(tmp_path, "audit", "BODY = 'real'\n")
    source = store / "block_registry" / "audit"
    actual = block_content_hash(source)
    recorded = "sha256:" + ("ab" * 32)
    lock = {
        "schema": "factory.blocks.lock.v1",
        "blocks": {
            "audit": {
                "id": "audit",
                "version": "1.0.0",
                "content_hash": recorded,
            }
        },
    }
    with pytest.raises(BlocksLockError) as exc:
        assert_block_matches_lock("audit", source, lock)
    msg = str(exc.value)
    assert "audit" in msg
    assert recorded in msg
    assert actual in msg


def test_unlocked_consumed_block_fails_hard(tmp_path):
    store = _store_with(tmp_path, "dashboard", "X = 1\n")
    lock = {"schema": "factory.blocks.lock.v1", "blocks": {}}
    with pytest.raises((BlocksLockError, RoleError), match="dashboard"):
        _clone(tmp_path, store, ("dashboard",), lock)


def test_matching_lock_allows_the_build(tmp_path):
    store = _store_with(tmp_path, "analytics", "VALUE = 1\n")
    lock = generate_lock(store, consumed_ids=["analytics"])
    ws, result = _clone(tmp_path, store, ("analytics",), lock)
    assert result.ok, result.detail
    assert (ws.destination / "vendor" / "blocks" / "analytics" / "block.py").is_file()


def test_generator_refuses_mismatched_store_hash(tmp_path):
    store = _store_with(tmp_path, "audit", "REAL = True\n")
    lock = generate_lock(store, consumed_ids=["audit"])
    recorded = "sha256:" + ("f" * 64)
    actual = lock["blocks"]["audit"]["content_hash"]
    lock["blocks"]["audit"]["content_hash"] = recorded

    bp = load_blueprint(BASIC)
    with pytest.raises(BlocksLockError) as exc:
        ProductGenerator(
            bp,
            blocks_root=store,
            blocks_lock=lock,
            factory_commit="t",
            blocks_commit="t",
        ).generate(tmp_path / "out")
    msg = str(exc.value)
    assert "audit" in msg
    assert recorded in msg
    assert actual in msg


def test_committed_lock_lists_every_consumed_block():
    from app.factory.blocks_lock import consumed_block_ids, default_lock_path, load_lock

    lock = load_lock(default_lock_path())
    assert lock["schema"] == "factory.blocks.lock.v1"
    assert lock["store"]["sha"].startswith("a372e76")
    ids = consumed_block_ids()
    assert ids, "factory shelf is empty"
    missing = [bid for bid in ids if bid not in lock["blocks"]]
    assert not missing, f"consumed block(s) missing from lock: {missing}"
    for bid, rec in lock["blocks"].items():
        assert rec["id"] == bid
        assert rec["version"]
        assert rec["content_hash"].startswith("sha256:")
        assert len(rec["content_hash"]) == len("sha256:") + 64


def test_update_lock_refresh_is_mechanical(tmp_path):
    from app.factory.blocks_lock import consumed_block_ids
    from app.factory.cli import main

    store = _store_with(tmp_path, "analytics", "PIN = 1\n")
    (store / "block_registry" / "dashboard").mkdir(parents=True)
    (store / "block_registry" / "dashboard" / "block.py").write_text(
        "PIN = 2\n", encoding="utf-8"
    )
    (store / "block_registry" / "dashboard" / "block.json").write_text(
        json.dumps({"id": "dashboard", "version": "2.0.0"}) + "\n",
        encoding="utf-8",
    )
    for bid in consumed_block_ids():
        d = store / "block_registry" / bid
        if (d / "block.json").is_file():
            continue
        d.mkdir(parents=True, exist_ok=True)
        (d / "block.py").write_text(f"STUB = {bid!r}\n", encoding="utf-8")
        (d / "block.json").write_text(
            json.dumps({"id": bid, "version": "1.0.0"}) + "\n", encoding="utf-8"
        )
    out = tmp_path / "blocks.lock.json"
    rc = main(
        [
            "update-lock",
            "--blocks-root",
            str(store),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["schema"] == "factory.blocks.lock.v1"
    assert lock["blocks"]["analytics"]["id"] == "analytics"
    assert lock["blocks"]["analytics"]["content_hash"] == block_content_hash(
        store / "block_registry" / "analytics"
    )
    assert lock["blocks"]["dashboard"]["version"] == "2.0.0"
    assert lock["blocks"]["dashboard"]["content_hash"] == block_content_hash(
        store / "block_registry" / "dashboard"
    )
