"""N1a live Cursor Background Agent helpers — mocked HTTP, no live network."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from subprocess import CompletedProcess
from urllib.error import URLError
from urllib.request import Request

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.builds_push import (
    BUILDS_TOKEN_ENV,
    BuildsPushError,
    BuildsRef,
    collect_branch,
    make_branch_name,
    parse_builds_repo,
    push_workspace,
)
from app.factory.build.cli_pivot import (
    CLASS_INFRA,
    EXECUTOR_UNAVAILABLE,
    ExecutorLaunch,
    compose_cbrief,
    decide_budget,
    launch_executor,
    run_cli_pivot,
)
from app.factory.build.cli_receipt import HANDOFF_TO_N3
from app.factory.build.cursor_ba import (
    CURSOR_API_BASE,
    LAUNCH_PROMPT,
    BackgroundAgentResult,
    create_agent,
    extract_spent_usd,
    poll_agent,
    run_background_agent,
)
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

IDS = ["analytics_surface", "dashboard_surface"]
RECEIPT = {"schema": "cli_receipt.v1", "cli_authored_ids": IDS}
PATHS = [f"app/actions/{cid}.py" for cid in IDS]


def _bp():
    return load_blueprint(SMOKE)


def _env(**extra: str) -> dict[str, str]:
    blob = {
        "CURSOR_API_KEY": "cursor-test-key",
        BUILDS_TOKEN_ENV: "builds-test-token",
    }
    blob.update(extra)
    return blob


class _Resp:
    def __init__(self, status: int, body):
        self.status = status
        if isinstance(body, (bytes, bytearray)):
            self._body = bytes(body)
        elif isinstance(body, str):
            self._body = body.encode("utf-8")
        else:
            self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class FakeOpener:
    def __init__(self, *, agent_status: str = "FINISHED", usage=None):
        self.calls: list[tuple[str, str, dict]] = []
        self.agent_status = agent_status
        self.usage = usage or {}
        self.create_body = {"id": "bc_test", "status": "CREATING"}

    def __call__(self, req: Request, timeout=None):
        method = req.get_method()
        url = req.full_url
        body = {}
        if req.data:
            body = json.loads(req.data.decode("utf-8"))
        self.calls.append((method, url, body))
        if url == f"{CURSOR_API_BASE}/v0/agents" and method == "POST":
            return _Resp(201, self.create_body)
        if url.startswith(f"{CURSOR_API_BASE}/v0/agents/") and method == "GET":
            payload = {
                "id": "bc_test",
                "status": self.agent_status,
                "target": {"branchName": "build/smoke-aaaa1111", "autoCreatePr": False},
            }
            payload.update(self.usage)
            return _Resp(200, payload)
        if "/contents/" in url:
            raw = base64.b64encode(json.dumps(RECEIPT).encode("utf-8")).decode("ascii")
            return _Resp(200, {"content": raw, "encoding": "base64"})
        if "/compare/" in url:
            return _Resp(
                200,
                {
                    "files": [
                        {
                            "filename": path,
                            "patch": "@@\n+def handle():\n+    return 1\n",
                        }
                        for path in PATHS + ["receipt.json"]
                    ]
                },
            )
        raise URLError(f"unexpected URL {url}")


class FakeGit:
    def __init__(self, *, fail_push: bool = False):
        self.calls: list[list[str]] = []
        self.fail_push = fail_push

    def __call__(self, args, *, cwd):
        self.calls.append(list(args))
        if args and args[0] == "push":
            if self.fail_push:
                return CompletedProcess(args, 1, "", "permission denied")
            return CompletedProcess(args, 0, "", "")
        if "rev-parse" in args:
            return CompletedProcess(args, 0, "abc123seedsha\n", "")
        return CompletedProcess(args, 0, "", "")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_branch_name_is_build_prefixed():
    name = make_branch_name("sess/Alpha One", suffix="deadbeef")
    assert name == "build/sess-Alpha-One-deadbeef"
    owner, repo, url = parse_builds_repo({})
    assert owner == "bopoadz-del"
    assert repo == "cerebrum-builds"
    assert url == "https://github.com/bopoadz-del/cerebrum-builds"
    owner, repo, url = parse_builds_repo(
        {"CEREBRUM_BUILDS_REPO": "https://github.com/acme/builds.git"}
    )
    assert (owner, repo) == ("acme", "builds")


def test_push_without_token_is_builds_error(tmp_path):
    with pytest.raises(BuildsPushError, match="CEREBRUM_BUILDS_GITHUB_TOKEN"):
        push_workspace(tmp_path, env={}, session_id="s")


def test_push_workspace_uses_git_and_redacts_on_failure(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "coder_brief.md").write_text("brief\n", encoding="utf-8")
    git = FakeGit(fail_push=True)
    with pytest.raises(BuildsPushError, match="push failed before agent start"):
        push_workspace(
            tmp_path,
            env=_env(),
            session_id="smoke",
            run_git=git,
            suffix="aaaa1111",
        )
    pushed = [c for c in git.calls if c and c[0] == "push"]
    assert pushed
    assert any("HEAD:build/smoke-aaaa1111" in " ".join(c) for c in pushed)


def test_create_agent_body_is_fixed_prompt_no_model():
    opener = FakeOpener()
    created = create_agent(
        api_key="k",
        repository_url="https://github.com/bopoadz-del/cerebrum-builds",
        ref="build/smoke-aaaa1111",
        opener=opener,
    )
    assert created["id"] == "bc_test"
    method, url, body = opener.calls[0]
    assert method == "POST"
    assert url == f"{CURSOR_API_BASE}/v0/agents"
    assert body["prompt"]["text"] == LAUNCH_PROMPT
    assert body["source"]["ref"] == "build/smoke-aaaa1111"
    assert body["target"]["autoCreatePr"] is False
    assert body["target"]["autoBranch"] is False
    assert "model" not in body


def test_poll_finished_then_collect():
    opener = FakeOpener()
    ref = BuildsRef(
        owner="bopoadz-del",
        repo="cerebrum-builds",
        repository_url="https://github.com/bopoadz-del/cerebrum-builds",
        branch="build/smoke-aaaa1111",
        seed_sha="abc123seedsha",
    )
    receipt, paths, diff = collect_branch(ref, env=_env(), opener=opener)
    assert receipt["cli_authored_ids"] == IDS
    assert "app/actions/analytics_surface.py" in paths
    assert "diff --git" in diff


def test_poll_hung_past_wall():
    opener = FakeOpener(agent_status="RUNNING")
    clock = Clock()

    def sleep(_s: float) -> None:
        clock.advance(40.0)

    polled = poll_agent(
        "bc_test",
        api_key="k",
        wall_s=50.0,
        start_timeout_s=90.0,
        poll_s=5.0,
        opener=opener,
        clock=clock,
        sleep=sleep,
    )
    assert polled["_hung"] is True
    assert polled["_started"] is True
    assert str(polled["status"]).upper() == "RUNNING"


def test_poll_never_left_creating():
    opener = FakeOpener(agent_status="CREATING")
    clock = Clock()

    def sleep(_s: float) -> None:
        clock.advance(20.0)

    with pytest.raises(Exception, match="never left CREATING"):
        poll_agent(
            "bc_test",
            api_key="k",
            wall_s=1000.0,
            start_timeout_s=15.0,
            poll_s=5.0,
            opener=opener,
            clock=clock,
            sleep=sleep,
        )


def test_poll_finished_after_wall_is_hung():
    opener = FakeOpener(agent_status="FINISHED")
    clock = Clock()

    def tick() -> float:
        clock.advance(60.0)
        return clock.t

    polled = poll_agent(
        "bc_test",
        api_key="k",
        wall_s=50.0,
        opener=opener,
        clock=tick,
        sleep=lambda _s: None,
    )
    assert polled["_hung"] is True
    assert str(polled["status"]).upper() == "FINISHED"


def test_poll_failed_is_infra():
    opener = FakeOpener(agent_status="FAILED")
    with pytest.raises(Exception, match="FAILED"):
        poll_agent(
            "bc_test",
            api_key="k",
            wall_s=100.0,
            opener=opener,
            clock=Clock(),
            sleep=lambda _s: None,
        )


def test_cursor_api_down_is_cursor_error():
    def down(req, timeout=None):
        raise URLError("connection refused")

    with pytest.raises(Exception, match="Cursor API down"):
        create_agent(
            api_key="k",
            repository_url="https://github.com/bopoadz-del/cerebrum-builds",
            ref="build/x",
            opener=down,
        )


def test_run_background_agent_success(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "coder_brief.md").write_text("brief\n", encoding="utf-8")
    opener = FakeOpener()
    result = run_background_agent(
        workspace=tmp_path,
        wall_s=1800.0,
        env=_env(),
        session_id="smoke",
        opener=opener,
        run_git=FakeGit(),
        sleep=lambda _s: None,
    )
    assert isinstance(result, BackgroundAgentResult)
    assert result.started is True
    assert result.hung is False
    assert result.receipt["cli_authored_ids"] == IDS
    assert result.changed_paths
    assert result.unified_diff
    assert any(u == f"{CURSOR_API_BASE}/v0/agents" for _m, u, _b in opener.calls)


def test_run_background_agent_hung_skips_collect(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "coder_brief.md").write_text("brief\n", encoding="utf-8")
    opener = FakeOpener(agent_status="RUNNING")
    clock = Clock()

    def sleep(_s: float) -> None:
        clock.advance(100.0)

    result = run_background_agent(
        workspace=tmp_path,
        wall_s=30.0,
        env=_env(),
        session_id="smoke",
        opener=opener,
        run_git=FakeGit(),
        clock=clock,
        sleep=sleep,
    )
    assert result.started is True
    assert result.hung is True
    assert result.receipt is None
    assert not any("/compare/" in u for _m, u, _b in opener.calls)


def test_launch_executor_missing_builds_token(tmp_path):
    bp = _bp()
    compiled = compose_cbrief(bp, plan=plan_blueprint(bp), budget_s=30.0)
    with pytest.raises(Exception, match="CEREBRUM_BUILDS_GITHUB_TOKEN"):
        launch_executor(
            brief=compiled,
            budget=decide_budget(wall_s=30.0),
            workspace=tmp_path,
            env={"CURSOR_API_KEY": "k"},
        )


def test_launch_executor_mocked_success(tmp_path, monkeypatch):
    bp = _bp()
    compiled = compose_cbrief(bp, plan=plan_blueprint(bp), budget_s=30.0)

    def fake_run(**_k):
        return BackgroundAgentResult(
            started=True,
            hung=False,
            elapsed_s=2.0,
            spent_usd=0.0,
            receipt=RECEIPT,
            changed_paths=PATHS,
            unified_diff="diff --git a/app/actions/analytics_surface.py "
            "b/app/actions/analytics_surface.py\n",
        )

    monkeypatch.setattr(
        "app.factory.build.cli_pivot.run_background_agent", fake_run
    )
    outcome = launch_executor(
        brief=compiled,
        budget=decide_budget(wall_s=30.0),
        workspace=tmp_path,
        env=_env(),
    )
    assert isinstance(outcome, ExecutorLaunch)
    assert outcome.started is True
    assert outcome.receipt["cli_authored_ids"] == IDS


def test_run_cli_pivot_mocked_ba_hands_to_n3(tmp_path, monkeypatch):
    def fake_run(**_k):
        return BackgroundAgentResult(
            started=True,
            receipt=RECEIPT,
            changed_paths=PATHS,
            unified_diff="\n".join(
                f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}" for p in PATHS
            ),
        )

    monkeypatch.setattr(
        "app.factory.build.cli_pivot.run_background_agent", fake_run
    )
    result = run_cli_pivot(
        _bp(),
        tmp_path / "ok",
        plan=plan_blueprint(_bp()),
        env=_env(),
    )
    assert result.honesty == HANDOFF_TO_N3
    assert result.green is False


def test_run_cli_pivot_api_down_is_unavailable(tmp_path, monkeypatch):
    def down(**_k):
        from app.factory.build.cursor_ba import CursorBAError

        raise CursorBAError("Cursor API down: connection refused")

    monkeypatch.setattr("app.factory.build.cli_pivot.run_background_agent", down)
    result = run_cli_pivot(
        _bp(),
        tmp_path / "down",
        plan=plan_blueprint(_bp()),
        env=_env(),
    )
    assert result.honesty == EXECUTOR_UNAVAILABLE
    assert result.failure_class == CLASS_INFRA
    assert "keyless prefix" not in result.detail
    assert "Cursor API down" in result.detail


def test_extract_spent_usd_reads_usage_else_zero():
    assert extract_spent_usd({}) == 0.0
    assert extract_spent_usd({"usage": {"spent_usd": 1.25}}) == 1.25
