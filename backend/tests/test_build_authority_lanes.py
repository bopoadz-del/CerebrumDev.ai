"""Build roles must be confined to their lane by code, not by prompt.

New-shape tests for the manufacturing kernel. The failure these guard against
is not a crash — it is a *quietly valid-looking* build: a WRITER that edits
the tests judging it, a TESTER that patches the code under test, or any role
that walks out of the workspace via ``..`` or a symlink and edits the factory
itself. Each of those produces an artifact that passes its gate and means
nothing, so every one of them has to raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.authority import (
    BUILD_PHASES,
    AuthorityError,
    BuildRole,
    LaneRoot,
    assert_phase_order,
    assert_write_allowed,
    authority_manifest,
    role_contract,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "product"
    ws.mkdir()
    return ws


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    st = tmp_path / "store"
    (st / "block_registry").mkdir(parents=True)
    return st


def test_collector_is_read_only(workspace):
    """The collector reports; materialising is the cloner's job."""
    with pytest.raises(AuthorityError, match="read-only"):
        assert_write_allowed(
            BuildRole.COLLECTOR, workspace / "vendor" / "x.py", workspace=workspace
        )


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    (
        (BuildRole.CLONER, "vendor/blocks/web/block.py", "app/actions/orders.py"),
        (BuildRole.WRITER, "app/actions/orders.py", "vendor/blocks/web/block.py"),
        (BuildRole.TESTER, "tests/test_orders.py", "app/actions/orders.py"),
    ),
)
def test_each_role_is_confined_to_its_lane(workspace, role, allowed, denied):
    assert assert_write_allowed(role, workspace / allowed, workspace=workspace)
    with pytest.raises(AuthorityError, match="its lanes are"):
        assert_write_allowed(role, workspace / denied, workspace=workspace)


def test_writer_cannot_touch_the_tests_that_judge_it(workspace):
    """The load-bearing separation: no self-grading."""
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.WRITER, workspace / "tests" / "test_smoke.py", workspace=workspace
        )


def test_tester_cannot_patch_the_code_under_test(workspace):
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.TESTER, workspace / "app" / "actions" / "orders.py", workspace=workspace
        )


def test_exact_file_lane_does_not_widen_to_siblings(workspace):
    """`blocks.lock.json` is a file lane, not a prefix."""
    assert assert_write_allowed(
        BuildRole.CLONER, workspace / "blocks.lock.json", workspace=workspace
    )
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.CLONER, workspace / "blocks.lock.json.bak", workspace=workspace
        )


def test_parent_traversal_out_of_the_workspace_is_refused(workspace):
    escape = workspace / "app" / ".." / ".." / "factory" / "generator.py"
    with pytest.raises(AuthorityError):
        assert_write_allowed(BuildRole.WRITER, escape, workspace=workspace)


def test_absolute_path_outside_the_workspace_is_refused(workspace, tmp_path):
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.WRITER, tmp_path / "elsewhere" / "app" / "x.py", workspace=workspace
        )


def test_symlink_out_of_the_workspace_is_refused(workspace, tmp_path):
    """A lane check on the unresolved path would pass this."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "app"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")

    with pytest.raises(AuthorityError):
        assert_write_allowed(BuildRole.WRITER, link / "escaped.py", workspace=workspace)


def test_vcs_directories_are_forbidden_even_inside_a_lane(workspace):
    """Rewriting .git would let a role forge the build's own provenance."""
    with pytest.raises(AuthorityError, match="version-control"):
        assert_write_allowed(
            BuildRole.CLONER, workspace / "vendor" / ".git" / "config", workspace=workspace
        )


def test_store_manager_writes_the_store_not_the_product(workspace, store):
    assert assert_write_allowed(
        BuildRole.STORE_MANAGER,
        store / "block_registry" / "invoice_parser" / "block.json",
        workspace=workspace,
        store_root=store,
    )
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.STORE_MANAGER,
            workspace / "app" / "actions" / "orders.py",
            workspace=workspace,
            store_root=store,
        )


def test_store_lane_is_inert_when_no_store_root_is_supplied(workspace, store):
    """A build that is not promoting upstream must not reach the store."""
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.STORE_MANAGER,
            store / "block_registry" / "x" / "block.json",
            workspace=workspace,
        )


def test_phases_run_in_order(workspace):
    assert_phase_order(BuildRole.COLLECTOR, [])
    assert_phase_order(BuildRole.WRITER, [BuildRole.COLLECTOR, BuildRole.CLONER])

    with pytest.raises(AuthorityError, match="CLONER"):
        assert_phase_order(BuildRole.WRITER, [BuildRole.COLLECTOR])
    with pytest.raises(AuthorityError, match="COLLECTOR"):
        assert_phase_order(BuildRole.TESTER, [BuildRole.CLONER, BuildRole.WRITER])


def test_manifest_covers_every_phase_and_marks_the_read_only_role():
    manifest = authority_manifest()
    assert manifest["phases"] == [p.value for p in BUILD_PHASES]
    assert set(manifest["roles"]) == {p.value for p in BUILD_PHASES}
    assert manifest["roles"]["COLLECTOR"]["read_only"] is True
    assert manifest["roles"]["WRITER"]["read_only"] is False
    # Every role states a gate — a phase with no gate is a phase that cannot fail.
    assert all(r["gate"] for r in manifest["roles"].values())


def test_every_phase_has_a_contract():
    for phase in BUILD_PHASES:
        contract = role_contract(phase)
        assert contract.mandate
        assert contract.gate
    assert role_contract("STORE_MANAGER").write_lanes[0][0] is LaneRoot.STORE
