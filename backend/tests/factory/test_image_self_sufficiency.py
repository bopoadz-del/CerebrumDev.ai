"""Image self-sufficiency, emulated (owner ruling 2026-10-08, item 4A).

The writer's pass must catch an image that omits what the app loads at runtime
(live: cycle 2 smoke B, an image with no vendor/) before any gate is dispatched.
The live Factory has no Docker, so the image is laid out from the Dockerfile's
COPY lines and .dockerignore, and the app is imported and every locked block
loaded through the product's own loader in a subprocess. These products are
synthetic: nothing here is a real blueprint.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build import image_sufficiency

DISPATCH = '''
from pathlib import Path


class BlockNotVendored(RuntimeError):
    pass


_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "blocks"


def load_block(block_id):
    path = _VENDOR / block_id / "block.py"
    if not path.is_file():
        raise BlockNotVendored(f"{block_id} is not vendored (looked in {path})")
    return path.read_text()
'''


def _product(root: Path, dockerfile: str, *, dockerignore: str = "", main: str = "VALUE = 1\n") -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "main.py").write_text(main, encoding="utf-8")
    (root / "app" / "dispatch.py").write_text(DISPATCH, encoding="utf-8")
    for block in ("ledger", "storage"):
        (root / "vendor" / "blocks" / block).mkdir(parents=True)
        (root / "vendor" / "blocks" / block / "block.py").write_text("X = 1\n", encoding="utf-8")
    (root / "blocks.lock.json").write_text(
        json.dumps({"blocks": {"ledger": {"path": "vendor/blocks/ledger"}, "storage": {"path": "vendor/blocks/storage"}}}),
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    if dockerignore:
        (root / ".dockerignore").write_text(dockerignore, encoding="utf-8")
    return root


FULL = "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nCMD [\"uvicorn\", \"app.main:app\"]\n"
APP_ONLY = "FROM python:3.12-slim\nWORKDIR /app\nCOPY app ./app\nCOPY blocks.lock.json ./\n"


def test_an_image_that_copies_the_whole_context_is_self_sufficient(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL))
    assert verdict.ok and verdict.judged, verdict.detail
    assert "2 locked blocks" in verdict.detail


def test_the_smoke_b_shape_is_caught_naming_each_missing_path(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, APP_ONLY))
    assert not verdict.ok and verdict.judged
    assert "/app/vendor/blocks/ledger/block.py" in verdict.missing
    assert "/app/vendor/blocks/storage/block.py" in verdict.missing
    assert "image missing /app/vendor/blocks/storage/block.py" in verdict.detail


def test_a_dockerignore_that_drops_the_runtime_tree_is_caught(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL, dockerignore="vendor/\n"))
    assert not verdict.ok
    assert any(p.endswith("/block.py") for p in verdict.missing)


def test_a_negated_dockerignore_rule_re_includes(tmp_path):
    ignore = "vendor/\n!vendor/blocks/**\n"
    verdict = image_sufficiency.check(_product(tmp_path, FULL, dockerignore=ignore))
    assert verdict.ok, verdict.detail


def test_explicit_copies_that_include_the_runtime_tree_pass(tmp_path):
    dockerfile = APP_ONLY + "COPY vendor ./vendor\n"
    verdict = image_sufficiency.check(_product(tmp_path, dockerfile))
    assert verdict.ok, verdict.detail


def test_an_app_module_the_image_lacks_is_named(tmp_path):
    root = _product(tmp_path, "FROM python:3.12-slim\nWORKDIR /app\nCOPY app/main.py ./app/main.py\n",
                    main="from app import helpers\n")
    (root / "app" / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    verdict = image_sufficiency.check(root)
    assert not verdict.ok and verdict.judged


def test_no_dockerfile_or_no_locked_block_is_not_judged_and_never_fails(tmp_path):
    root = _product(tmp_path, FULL)
    (root / "Dockerfile").unlink()
    verdict = image_sufficiency.check(root)
    assert verdict.ok and not verdict.judged
    (root / "Dockerfile").write_text(FULL, encoding="utf-8")
    (root / "blocks.lock.json").write_text(json.dumps({"blocks": {}}), encoding="utf-8")
    verdict = image_sufficiency.check(root)
    assert verdict.ok and not verdict.judged


def test_a_copy_from_another_stage_is_not_judged(tmp_path):
    dockerfile = "FROM python:3.12 AS build\nRUN true\nFROM python:3.12-slim\nWORKDIR /app\nCOPY --from=build /x /app\n"
    verdict = image_sufficiency.check(_product(tmp_path, dockerfile))
    assert verdict.ok and not verdict.judged


def test_a_third_party_package_the_factory_lacks_is_not_judged(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL, main="import zz_not_installed_pkg\n"))
    assert verdict.ok and not verdict.judged


def test_the_writer_gate_runs_it_and_names_the_path(tmp_path, monkeypatch):
    """The WRITER gate refuses with image_missing_runtime_path and one finding
    per missing path -- rework before any gate dispatch."""
    from app.factory.build import gates
    from app.factory.build.authority import BuildRole

    root = _product(tmp_path, APP_ONLY)
    ok = gates.GateResult(ok=True, gate="x", detail="ok")
    monkeypatch.setattr(
        "app.factory.build.authorship.agent_written_handler_ids_in_workspace", lambda _ws: {"cap"}
    )
    monkeypatch.setattr(gates, "gate_workspace_compiles", lambda _ctx: ok)
    monkeypatch.setattr(gates, "gate_writer_behaviour", lambda _ctx: ok)
    monkeypatch.setattr(gates, "gate_ui_surface", lambda _ctx: ok)
    monkeypatch.setattr("app.factory.build.ui_e2e.gate_ui_end_to_end", lambda _ctx: ok)

    class _Money:
        status, reason, findings = "PASS", "", []

    monkeypatch.setattr("app.factory.build.money_contract.money_verdict", lambda _ws: _Money())
    result = gates.gate_writer_contract(gates.GateContext(workspace=root, role=BuildRole.WRITER))
    assert result.ok is False, result.detail
    assert result.reason == "image_missing_runtime_path"
    assert any("image missing /app/vendor/blocks/storage/block.py" in f for f in result.findings)


def test_nothing_here_names_a_product_block_or_directory():
    """No hardwiring: the module reads which blocks, which paths and which
    files to copy from the product's own data."""
    source = Path(image_sufficiency.__file__).read_text(encoding="utf-8")
    for literal in ('"vendor"', "'vendor'", '"storage"', '"analytics"', '"/app"'):
        assert literal not in source, literal
