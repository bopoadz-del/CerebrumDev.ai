"""N1b deterministic receipt-vs-diff + path-jail. Two failure classes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import CapabilitySpec, FactoryScenario, ProductBlueprint
from app.factory.build.cli_pivot import (
    CLASS_CONTENT,
    CLASS_INFRA,
    EXECUTOR_UNAVAILABLE,
    ExecutorLaunch,
    run_cli_pivot,
)
from app.factory.build.authority import AuthorityError, BuildRole, assert_write_allowed
from app.factory.build.cli_receipt import (
    HANDOFF_TO_N3,
    PATHS_VIOLATED,
    RECEIPT_INVALID,
    PathsViolated,
    ReceiptInvalid,
    _posix,
    ba_allowed_globs,
    blueprint_capability_set,
    enforce_receipt,
    handler_relpath,
    load_receipt,
    parse_changed_paths,
    writer_allowed_globs,
)
from app.factory.build.ledger import BuildLedger
from app.factory.product_architect import plan_blueprint


def _blueprint(*ids: str) -> ProductBlueprint:
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="n1b-fixture",
        product_name="N1b Fixture",
        vertical="product",
        summary="Receipt jail fixture.",
        capabilities=[
            CapabilitySpec(id=cid, description=f"{cid} handler") for cid in ids
        ],
        factory_scenario=FactoryScenario.CREATE_PRODUCT,
    )


def test_blueprint_set_is_exact():
    bp = _blueprint("alpha", "beta")
    assert blueprint_capability_set(bp) == {"alpha", "beta"}


def test_malformed_or_missing_receipt_is_invalid(tmp_path):
    with pytest.raises(ReceiptInvalid, match=RECEIPT_INVALID):
        load_receipt(None)
    with pytest.raises(ReceiptInvalid, match="not well-formed"):
        load_receipt("{")
    with pytest.raises(ReceiptInvalid, match="missing cli_authored_ids"):
        load_receipt({"schema": "cli_receipt.v1"})
    with pytest.raises(ReceiptInvalid, match="must be a list"):
        load_receipt({"cli_authored_ids": "alpha"})
    with pytest.raises(ReceiptInvalid, match="receipt.json missing"):
        enforce_receipt(
            blueprint=_blueprint("alpha"),
            workspace=tmp_path,
            changed_paths=["app/actions/alpha.py"],
        )


def test_missing_and_extra_ids_are_no_partial_credit():
    bp = _blueprint("alpha", "beta")
    with pytest.raises(ReceiptInvalid, match="missing=beta"):
        enforce_receipt(
            blueprint=bp,
            receipt={"cli_authored_ids": ["alpha"]},
            changed_paths=["app/actions/alpha.py"],
        )
    with pytest.raises(ReceiptInvalid, match="extra=gamma"):
        enforce_receipt(
            blueprint=bp,
            receipt={"cli_authored_ids": ["alpha", "beta", "gamma"]},
            changed_paths=[
                "app/actions/alpha.py",
                "app/actions/beta.py",
                "app/actions/gamma.py",
            ],
        )
    with pytest.raises(ReceiptInvalid, match="missing=beta"):
        enforce_receipt(
            blueprint=bp,
            receipt={"cli_authored_ids": ["alpha"]},
            changed_paths=[
                "app/actions/alpha.py",
                "tests/test_alpha.py",
            ],
        )


def test_claimed_id_must_appear_as_a_real_diff_file():
    bp = _blueprint("alpha")
    with pytest.raises(ReceiptInvalid, match="not in the branch diff"):
        enforce_receipt(
            blueprint=bp,
            receipt={"cli_authored_ids": ["alpha"]},
            changed_paths=["app/models/alpha.py"],
        )
    verdict = enforce_receipt(
        blueprint=bp,
        receipt={
            "cli_authored_ids": ["alpha"],
            "path_by_id": {"alpha": "app/actions/custom_alpha.py"},
        },
        changed_paths=["app/actions/custom_alpha.py"],
    )
    assert verdict.honesty == HANDOFF_TO_N3
    assert verdict.green is False
    assert verdict.next == "n3_gate"


def test_unified_diff_is_the_check():
    diff = """
diff --git a/app/actions/alpha.py b/app/actions/alpha.py
--- a/app/actions/alpha.py
+++ b/app/actions/alpha.py
@@ -1 +1 @@
-old
+new
"""
    assert parse_changed_paths(unified_diff=diff) == ["app/actions/alpha.py"]
    verdict = enforce_receipt(
        blueprint=_blueprint("alpha"),
        receipt={"cli_authored_ids": ["alpha"]},
        unified_diff=diff,
    )
    assert verdict.ok is True
    assert verdict.green is False


def test_posix_keeps_dot_git_and_dot_env():
    assert _posix(".git/config") == ".git/config"
    assert _posix("./.git/config") == ".git/config"
    assert _posix("./app/actions/alpha.py") == "app/actions/alpha.py"
    assert _posix(".env.example") == ".env.example"


def test_ba_allowed_globs_include_tests_not_sealed():
    """Option C Hybrid: BA jail allows tests/**; sealed trees stay out."""
    allowed = ba_allowed_globs()
    assert allowed == writer_allowed_globs()
    assert "tests/**" in allowed
    assert "app/**" in allowed
    for sealed in (
        "vendor/**",
        "vendor_blocks/**",
        "blocks.lock.json",
        "build_ledger.jsonl",
        ".git/**",
    ):
        assert sealed not in allowed


def test_in_process_writer_jail_stays_sealed_off_tests(tmp_path):
    """Option C Hybrid: Factory WRITER lanes do not gain tests/** until N2."""
    from app.factory.build.authority import ROLE_CONTRACTS

    lanes = [glob for _root, glob in ROLE_CONTRACTS[BuildRole.WRITER].write_lanes]
    assert "tests/**" not in lanes
    (tmp_path / "app" / "actions").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "actions" / "alpha.py").write_text("# ok\n", encoding="utf-8")
    (tmp_path / "tests" / "test_alpha.py").write_text("# no\n", encoding="utf-8")
    assert assert_write_allowed(
        BuildRole.WRITER, tmp_path / "app" / "actions" / "alpha.py", workspace=tmp_path
    )
    with pytest.raises(AuthorityError, match="may not write"):
        assert_write_allowed(
            BuildRole.WRITER, tmp_path / "tests" / "test_alpha.py", workspace=tmp_path
        )


def test_writing_under_tests_is_handoff_not_paths_violated():
    """cli-pivot BA may land tests/** next to handlers (Option C Hybrid)."""
    verdict = enforce_receipt(
        blueprint=_blueprint("alpha"),
        receipt={"cli_authored_ids": ["alpha"]},
        changed_paths=[
            handler_relpath("alpha"),
            "tests/test_alpha.py",
            "tests/factory/test_alpha_roundtrip.py",
            ".env.example",
        ],
    )
    assert verdict.honesty == HANDOFF_TO_N3
    assert verdict.green is False
    assert verdict.next == "n3_gate"


def test_vendored_and_outside_paths_are_paths_violated():
    bp = _blueprint("alpha")
    receipt = {"cli_authored_ids": ["alpha"]}
    with pytest.raises(PathsViolated, match="read-only"):
        enforce_receipt(
            blueprint=bp,
            receipt=receipt,
            changed_paths=[
                handler_relpath("alpha"),
                "vendor/estate_registry/block.py",
            ],
        )
    with pytest.raises(PathsViolated, match="vendor_blocks_mirror"):
        enforce_receipt(
            blueprint=bp,
            receipt=receipt,
            changed_paths=[
                handler_relpath("alpha"),
                "vendor_blocks_mirror/estate_registry/block.py",
            ],
        )
    for sealed in (
        "blocks.lock.json",
        "build_ledger.jsonl",
        ".git/config",
        "vendor_blocks/estate_registry/block.py",
    ):
        with pytest.raises(PathsViolated, match="read-only"):
            enforce_receipt(
                blueprint=bp,
                receipt=receipt,
                changed_paths=[handler_relpath("alpha"), sealed],
            )
    with pytest.raises(PathsViolated, match="outside allowed paths"):
        enforce_receipt(
            blueprint=bp,
            receipt=receipt,
            changed_paths=[
                handler_relpath("alpha"),
                "secrets/token.txt",
            ],
        )


def test_receipt_file_on_disk(tmp_path):
    bp = _blueprint("alpha")
    (tmp_path / "receipt.json").write_text(
        json.dumps({"cli_authored_ids": ["alpha"]}),
        encoding="utf-8",
    )
    verdict = enforce_receipt(
        blueprint=bp,
        workspace=tmp_path,
        changed_paths=["app/actions/alpha.py"],
    )
    assert verdict.honesty == HANDOFF_TO_N3
    assert verdict.green is False


def test_seam_wires_content_vs_infra_to_the_ledger(tmp_path):
    from app.factory.blueprint import load_blueprint

    smoke = load_blueprint(
        Path(__file__).resolve().parents[3]
        / "blueprints/examples/runner_smoke.yaml"
    )
    plan = plan_blueprint(smoke)

    content = run_cli_pivot(
        smoke,
        tmp_path / "content",
        plan=plan,
        launch=lambda **_k: ExecutorLaunch(
            started=True,
            receipt={"cli_authored_ids": ["analytics_surface"]},
            changed_paths=["app/actions/analytics_surface.py"],
        ),
    )
    assert content.honesty == RECEIPT_INVALID
    assert content.failure_class == CLASS_CONTENT
    jail = run_cli_pivot(
        smoke,
        tmp_path / "jail",
        plan=plan,
        launch=lambda **_k: ExecutorLaunch(
            started=True,
            receipt={
                "cli_authored_ids": [
                    "analytics_surface",
                    "dashboard_surface",
                ]
            },
            changed_paths=[
                "app/actions/analytics_surface.py",
                "app/actions/dashboard_surface.py",
                "vendor/secret.py",
            ],
        ),
    )
    assert jail.honesty == PATHS_VIOLATED
    assert jail.failure_class == CLASS_CONTENT
    infra = run_cli_pivot(
        smoke,
        tmp_path / "infra",
        plan=plan,
        env={},
    )
    assert infra.honesty == EXECUTOR_UNAVAILABLE
    assert infra.failure_class == CLASS_INFRA

    def _classes(folder: str) -> set[str]:
        return {
            str((e.payload or {}).get("class") or "")
            for e in BuildLedger(tmp_path / folder / "build_ledger.jsonl").events()
            if (e.payload or {}).get("honesty")
        }

    assert CLASS_CONTENT in _classes("content")
    assert CLASS_CONTENT in _classes("jail")
    assert CLASS_INFRA in _classes("infra")
    assert CLASS_INFRA not in _classes("content")
    assert CLASS_CONTENT not in _classes("infra")
