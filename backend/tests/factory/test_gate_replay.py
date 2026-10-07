"""Gate replay (owner rule, 2026-10-08): a gate change is replayed before
merge against the builds the gate already certified.

The Factory half: re-render every Factory-written file onto a certified tree
with the PR's code, route the replayed result through the live ingest rule,
and report cerebrum-builds' replay run as this PR's check. Branch selection
and the flip rule live with the gate in cerebrum-builds
(.github/store_gate/replay_select.py, replay_flips.py) and are tested there.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import yaml

from app.factory.build import gate_replay
from app.factory.build.n3_store_gate import N3_STORE_GATE_GREEN
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    ACCEPTANCE_SCRIPT_REL,
    render_acceptance_script,
)

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "gate-replay.yml"
BUILD = REPO / "backend" / "app" / "factory" / "build"
#: The modules the gate's Factory half is made of (their own imports from
#: app.factory.build are derived below, never listed).
GATE_ROOTS = (
    "gate_replay",
    "stamp_registry",
    "n3_store_gate",
    "factory_refresh",
    "store_acceptance",
    "acceptance_floor",
)


def _gate_modules() -> set:
    found = set(GATE_ROOTS)
    for name in GATE_ROOTS:
        tree = ast.parse((BUILD / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.factory.build."):
                found.add(node.module.rsplit(".", 1)[1])
    return found


def _workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    data["on"] = data.get("on", data.get(True))
    return data


# -- config ---------------------------------------------------------------------


def test_n_comes_from_the_config(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / gate_replay.CONFIG_REL).write_text(json.dumps({"certified_branches": 5}), encoding="utf-8")
    assert gate_replay.replay_config(tmp_path)["certified_branches"] == 5


def test_n_defaults_to_three_without_a_valid_config(tmp_path):
    assert gate_replay.replay_config(tmp_path)["certified_branches"] == 3
    (tmp_path / ".github").mkdir()
    (tmp_path / gate_replay.CONFIG_REL).write_text('{"certified_branches": "many"}', encoding="utf-8")
    assert gate_replay.replay_config(tmp_path)["certified_branches"] == 3


def test_the_repo_config_is_the_one_read():
    assert gate_replay.replay_config()["certified_branches"] == json.loads(
        (REPO / gate_replay.CONFIG_REL).read_text(encoding="utf-8")
    )["certified_branches"]


# -- the workflow ---------------------------------------------------------------


def test_every_gate_module_triggers_the_replay():
    paths = set(_workflow()["on"]["pull_request"]["paths"])
    missing = sorted(
        f"backend/app/factory/build/{m}.py"
        for m in _gate_modules()
        if f"backend/app/factory/build/{m}.py" not in paths
    )
    assert not missing, f"gate modules that would change the gate without a replay: {missing}"
    from app.factory.build.acceptance_floor import floor_path

    floor = floor_path().relative_to(REPO).as_posix()
    assert floor in paths
    assert ".github/workflows/gate-replay.yml" in paths
    assert gate_replay.CONFIG_REL.as_posix() in paths


def test_the_replay_is_one_named_check_that_dispatches_with_the_pr_head():
    jobs = _workflow()["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["name"] == "gate replay (certified builds)"
    runs = " ".join(str(step.get("run") or "") for step in job["steps"])
    assert "gate_replay dispatch" in runs
    assert "github.event.pull_request.head.sha" in runs
    env = {k: v for step in job["steps"] for k, v in (step.get("env") or {}).items()}
    from app.factory.build.builds_push import BUILDS_TOKEN_ENV

    assert BUILDS_TOKEN_ENV in env


# -- render ---------------------------------------------------------------------


def _certified_tree(root: Path) -> Path:
    for rel, text in {
        "app/__init__.py": "",
        "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "requirements.txt": "fastapi\nuvicorn\n",
        # A harness an older Factory rendered.
        str(ACCEPTANCE_SCRIPT_REL): "# an older Factory's harness\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    dna = root / "product-dna"
    dna.mkdir()
    shutil.copy(REPO / "blueprints" / "examples" / "basic_product.yaml", dna / "product_blueprint.yaml")
    return root


def test_render_puts_this_factorys_brief_aware_harness_on_the_tree(tmp_path):
    root = _certified_tree(tmp_path)
    blueprint = gate_replay.load_tree_blueprint(root)
    assert blueprint is not None

    changed = gate_replay.render_onto(root)

    assert ACCEPTANCE_SCRIPT_REL.as_posix() in changed
    assert (root / ACCEPTANCE_SCRIPT_REL).read_text(encoding="utf-8") == render_acceptance_script(blueprint)
    # The product's own code is never a Factory file.
    assert "app/main.py" not in changed


def test_render_twice_changes_nothing_the_second_time(tmp_path):
    root = _certified_tree(tmp_path)
    gate_replay.render_onto(root)
    assert gate_replay.render_onto(root) == []


# -- verdict --------------------------------------------------------------------


def _payload(failing=(), score=None) -> dict:
    lines = [
        {"name": n, "status": "FAIL" if n in failing else "PASS", "detail": "x"}
        for n in ACCEPTANCE_CHECK_NAMES
    ]
    passed = ACCEPTANCE_REQUIRED - len(failing)
    return {
        "ok": not failing and score is None,
        "passed": passed,
        "total": ACCEPTANCE_REQUIRED,
        "score": score or f"{passed}/{ACCEPTANCE_REQUIRED}",
        "lines": lines,
        "sha": "a" * 40,
        "via": "scripts/acceptance.py-in-docker",
    }


def test_a_certified_result_routes_green(tmp_path):
    out = gate_replay.verdict(_certified_tree(tmp_path), _payload(), branch="build/x")
    assert out["ok"] is True
    assert out["honesty"] == N3_STORE_GATE_GREEN


def test_a_red_result_never_routes_green(tmp_path):
    out = gate_replay.verdict(_certified_tree(tmp_path), _payload(failing=("no_token_401",)), branch="build/x")
    assert out["ok"] is False


def test_an_unscored_result_never_routes_green(tmp_path):
    payload = {"ok": False, "passed": 0, "total": 0, "score": "no score", "lines": [], "sha": "b" * 40}
    out = gate_replay.verdict(_certified_tree(tmp_path), payload, branch="build/x")
    assert out["ok"] is False


# -- dispatch -------------------------------------------------------------------


class _GitHub:
    def __init__(self, conclusion="success", dispatch_status=204):
        self.conclusion = conclusion
        self.dispatch_status = dispatch_status
        self.request = ""
        self.calls = []

    def __call__(self, method, path, *, token, body=None, **_kw):
        self.calls.append((method, path, body))
        if method == "POST":
            self.request = body["inputs"]["request"]
            return self.dispatch_status, {}
        if path.startswith("/repos/o/r/actions/workflows/"):
            return 200, {
                "workflow_runs": [
                    {"id": 7, "display_title": "gate replay someone-else", "status": "completed", "conclusion": "failure"},
                    {
                        "id": 9,
                        "display_title": f"gate replay {self.request}",
                        "status": "completed",
                        "conclusion": self.conclusion,
                        "html_url": "https://example.invalid/run/9",
                    },
                ]
            }
        return 404, {}


ENV = {"CEREBRUM_BUILDS_GITHUB_TOKEN": "t", "CEREBRUM_BUILDS_REPO": "o/r"}
CFG = {"certified_branches": 4, "builds_ref": "main"}


def test_dispatch_reports_the_replay_runs_conclusion():
    gh = _GitHub("success")
    out = gate_replay.dispatch_and_wait("f" * 40, env=ENV, request_fn=gh, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is True and "run/9" in out["detail"]
    method, path, body = gh.calls[0]
    assert path.endswith(f"/actions/workflows/{gate_replay.REPLAY_WORKFLOW}/dispatches")
    assert body["inputs"]["factory_ref"] == "f" * 40
    assert body["inputs"]["certified_branches"] == "4"


def test_a_failed_replay_fails_the_check():
    gh = _GitHub("failure")
    out = gate_replay.dispatch_and_wait("f" * 40, env=ENV, request_fn=gh, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is False


def test_a_refused_dispatch_fails_the_check_with_the_answer():
    gh = _GitHub(dispatch_status=403)
    out = gate_replay.dispatch_and_wait("f" * 40, env=ENV, request_fn=gh, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is False and "HTTP 403" in out["detail"]


def test_no_token_fails_the_check():
    out = gate_replay.dispatch_and_wait("f" * 40, env={}, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is False
