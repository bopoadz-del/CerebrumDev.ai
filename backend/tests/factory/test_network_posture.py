"""S7: exactly one network posture — P1 offline strict."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.network_posture import (
    NETWORK_POSTURE,
    P1_CAPTURE_ADAPTER,
    P1_ENV_EXAMPLE,
    P1_FORBIDDEN,
    P1_SOCKET_BLOCKER_MARKERS,
    POSTURE_ID,
    REJECTED_ALTERNATIVES,
    apply_p1_capture_manifest,
    assert_workspace_posture,
)
from app.factory.build.roles import RoleContext, _CONFTEST, run_cloner
from app.factory.build.runner import RoleRunner
from app.factory.build.workspace import RoleWorkspace

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
MIRROR_CAPTURE_JSON = (
    ROOT / "backend/app/factory/vendor_blocks_mirror/capture/block.json"
)
MIRROR_CAPTURE_PY = ROOT / "backend/app/factory/vendor_blocks_mirror/capture/block.py"


def test_chosen_posture_is_p1():
    assert NETWORK_POSTURE == "P1"
    assert POSTURE_ID == "P1"
    assert "P2" in REJECTED_ALTERNATIVES
    assert "P3" in REJECTED_ALTERNATIVES
    assert "blocker" in REJECTED_ALTERNATIVES["P3"].lower()


def test_socket_blocker_markers_unchanged():
    for marker in P1_SOCKET_BLOCKER_MARKERS:
        assert marker in _CONFTEST
    assert "socket.socket.connect = _offline_connect" in _CONFTEST
    assert "P1" in _CONFTEST


def test_vendor_mirror_capture_json_defaults_are_p1():
    data = json.loads(MIRROR_CAPTURE_JSON.read_text(encoding="utf-8"))
    assert data["permissions"]["network"] is False
    providers = {
        item["name"]: item.get("default")
        for item in data["inputs"]
        if isinstance(item, dict)
    }
    assert providers["llm_provider"] == "none"
    assert providers["ocr_engine"] == "tesseract"
    assert providers["ollama_base_url"] == ""
    assert providers["store_captures"] is False
    assert "get_block" in MIRROR_CAPTURE_PY.read_text(encoding="utf-8")


def test_apply_p1_manifest_does_not_enable_network():
    rewritten = apply_p1_capture_manifest(
        {
            "permissions": {"network": False},
            "inputs": [{"name": "llm_provider", "default": "deepseek"}],
        }
    )
    assert rewritten["permissions"]["network"] is False
    assert rewritten["inputs"][0]["default"] == "none"


def test_p1_capture_run_is_scripted_not_echo(tmp_path):
    path = tmp_path / "p1_capture.py"
    path.write_text(P1_CAPTURE_ADAPTER, encoding="utf-8")
    ns: dict = {}
    exec(path.read_text(encoding="utf-8"), ns)
    out = ns["run"](text="Reach me at ops@example.com https://local.test 42")
    assert out["posture"] == "P1"
    assert out["llm_provider"] == "none"
    assert out["raw_text"]
    assert "ops@example.com" in out["entities"]["emails"]
    assert out["capture_id"] != "Reach me at ops@example.com https://local.test 42"
    assert "import httpx" not in P1_CAPTURE_ADAPTER
    assert 'llm_provider": "none"' in P1_CAPTURE_ADAPTER or 'llm_provider": "none"' in str(out)


def test_cloner_emits_p1_capture_without_blocks_root(tmp_path):
    src = tmp_path / "src" / "capture"
    src.mkdir(parents=True)
    (src / "block.json").write_text(
        json.dumps(
            {
                "id": "capture",
                "permissions": {"network": False},
                "inputs": [
                    {"name": "llm_provider", "default": "deepseek"},
                    {"name": "ocr_engine", "default": "tesseract"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (src / "block.py").write_text(
        "from app.blocks import get_block\n\ndef run(**kwargs):\n    return get_block('capture')()\n",
        encoding="utf-8",
    )
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=None,
        state={"resolved_blocks": ("capture",)},
    )
    import app.factory.build.roles as roles_mod

    original = roles_mod._block_source_dir
    roles_mod._block_source_dir = lambda bid, root: src
    try:
        result = run_cloner(ctx)
    finally:
        roles_mod._block_source_dir = original
    assert result.ok, result.detail
    shipped = ws.read_text(Path("vendor") / "blocks" / "capture" / "block.py")
    assert "P1" in shipped
    assert "get_block" not in shipped
    meta = json.loads(ws.read_text(Path("vendor") / "blocks" / "capture" / "block.json"))
    defaults = {i["name"]: i.get("default") for i in meta["inputs"]}
    assert defaults["llm_provider"] == "none"
    assert meta["permissions"]["network"] is False


def test_role_runner_tree_is_p1(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    out = tmp_path / "build"
    result = RoleRunner(load_blueprint(SMOKE), out).run()
    assert result.ok, result.to_dict()
    text = (out / ".env.example").read_text(encoding="utf-8")
    assert text == P1_ENV_EXAMPLE
    for token in P1_FORBIDDEN:
        assert token not in text
    assert_workspace_posture(out)
