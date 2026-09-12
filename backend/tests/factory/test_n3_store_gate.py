"""N3 store-gate ingest: 12/12 success, red, timeout, no Continue-into-WRITER."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request

from app.factory.build.authority import BuildRole
from app.factory.build.cli_receipt import HANDOFF_TO_N3
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import (
    N3_STORE_GATE_FAILED,
    N3_STORE_GATE_GREEN,
    N3_STORE_GATE_MISSING,
    N3_STORE_GATE_TIMEOUT,
    BuildsTarget,
    fetch_store_gate_status,
    handoff_awaiting_n3,
    ingest_n3_store_gate,
    report_from_store_gate_payload,
    wait_for_store_gate,
)
from app.factory.build.store_acceptance import ACCEPTANCE_REQUIRED, acceptance_is_kk
from app.factory.build_jobs import build_status, is_build_complete
from app.factory import platform_chat_flow
from app.models.session import ProductDesignState, SessionState

SHA = "081a52874bb0141c5eb730b01f26dcc0bf8d0fa2"
BRANCH = "build/sess_02af51453b364e3f-278c481a"
IDS = ["audit", "finance_ops_core", "team"]
ENV = {
    "CEREBRUM_BUILDS_GITHUB_TOKEN": "builds-test-token",
    "CEREBRUM_BUILDS_REPO": "bopoadz-del/cerebrum-builds",
}


class _Resp:
    def __init__(self, status: int, body):
        self.status = status
        raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        self._body = raw

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class StatusOpener:
    def __init__(
        self,
        *,
        state: str = "success",
        description: str = "acceptance.py in Docker 12/12",
        statuses=None,
        refs=None,
        commit_sha: str = SHA,
        status_http: int = 200,
    ):
        self.calls: list[tuple[str, str]] = []
        self.state = state
        self.description = description
        self.statuses = statuses
        self.refs = refs
        self.commit_sha = commit_sha
        self.status_http = status_http

    def __call__(self, req: Request, timeout=None):
        method = req.get_method()
        url = req.full_url
        self.calls.append((method, url))
        if "/statuses" in url:
            rows = self.statuses
            if rows is None:
                rows = [
                    {
                        "context": "store-gate",
                        "state": self.state,
                        "description": self.description,
                    }
                ]
            return _Resp(self.status_http, rows)
        if "/matching-refs/" in url:
            return _Resp(
                200,
                self.refs
                if self.refs is not None
                else [
                    {
                        "ref": f"refs/heads/{BRANCH}",
                        "object": {"sha": SHA, "type": "commit"},
                    }
                ],
            )
        if "/commits/" in url:
            return _Resp(200, {"sha": self.commit_sha})
        raise AssertionError(f"unexpected GitHub URL {url}")


def _handoff_ledger(
    tmp_path: Path,
    *,
    sha: str = SHA,
    branch: str = BRANCH,
    ids=None,
) -> Path:
    out = tmp_path / "sessions" / "sess_02af51453b364e3f" / "finance-ops"
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="finance-ops", inputs_hash="handoff-hash")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok", payload={"gate": "seed"})
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="HANDOFF_TO_N3: store-gate next",
        payload={
            "honesty": HANDOFF_TO_N3,
            "seam": "cli_pivot",
            "next": "n3_gate",
            "green": False,
            "cli_authored_ids": list(ids or IDS),
            "builds_sha": sha,
            "builds_branch": branch,
            "builds_owner": "bopoadz-del",
            "builds_repo": "cerebrum-builds",
        },
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="HANDOFF_TO_N3: store-gate next",
        payload={
            "outcome": HANDOFF_TO_N3,
            "honesty": HANDOFF_TO_N3,
            "next": "n3_gate",
            "green": False,
            "cycle": "code",
            "pilot_ready": False,
            "cli_authored_ids": list(ids or IDS),
            "builds_sha": sha,
            "builds_branch": branch,
        },
    )
    (out / "docs").mkdir(exist_ok=True)
    (out / "docs" / "build_provenance.json").write_text(
        json.dumps(
            {
                "brief_dispatch": {
                    "cli_authored_ids": list(ids or IDS),
                    "n_required": len(ids or IDS),
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


def _session_for(out: Path) -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    state = SessionState(
        session_id="sess_02af51453b364e3f",
        user_id="user-1",
        account_id="acct-1",
    )
    state.product_design = ProductDesignState()
    bp = draft_blueprint_from_brief("build a finance operations platform")
    state.product_design.blueprint = bp.model_dump(mode="json")
    state.product_design.blueprint_approved = True
    state.product_design.generation = {
        "output_dir": str(out),
        "inputs_hash": "handoff-hash",
        "product_id": "finance-ops",
        "engine": "runner",
        "build": {"state": "failed", "outcome": HANDOFF_TO_N3},
    }
    return state


def test_handoff_is_awaiting_not_terminal(tmp_path):
    out = _handoff_ledger(tmp_path)
    assert handoff_awaiting_n3(out) is True
    status = build_status(out)
    assert status["state"] == "building"
    assert status["honesty"] == HANDOFF_TO_N3
    assert status["n3_waiting"] is True
    assert status["pilot_ready"] is False
    assert is_build_complete(out) is False
    state = _session_for(out)
    assert platform_chat_flow.is_handoff_awaiting_n3(state) is True
    assert platform_chat_flow.is_generation_terminal_failure(state) is False
    assert platform_chat_flow.is_generation_complete(state) is False


def test_fetch_store_gate_success_12_12():
    target = BuildsTarget(
        owner="bopoadz-del", repo="cerebrum-builds", sha=SHA, branch=BRANCH
    )
    snap = fetch_store_gate_status(target, env=ENV, opener=StatusOpener())
    assert snap.is_12_of_12 is True
    assert snap.state == "success"
    assert snap.score == "12/12"


def test_fetch_store_gate_red_is_not_green():
    target = BuildsTarget(
        owner="bopoadz-del", repo="cerebrum-builds", sha=SHA, branch=BRANCH
    )
    snap = fetch_store_gate_status(
        target,
        env=ENV,
        opener=StatusOpener(
            state="failure", description="acceptance.py in Docker 3/12"
        ),
    )
    assert snap.is_12_of_12 is False
    assert snap.state == "failure"
    assert snap.pending is False
    assert snap.score == "3/12"


def test_fetch_store_gate_success_wrong_score_fail_closed():
    target = BuildsTarget(
        owner="bopoadz-del", repo="cerebrum-builds", sha=SHA, branch=BRANCH
    )
    snap = fetch_store_gate_status(
        target,
        env=ENV,
        opener=StatusOpener(
            state="success", description="acceptance.py in Docker 11/12"
        ),
    )
    assert snap.state == "success"
    assert snap.is_12_of_12 is False
    assert snap.ok is False


def test_fetch_store_gate_missing_is_pending():
    target = BuildsTarget(
        owner="bopoadz-del", repo="cerebrum-builds", sha=SHA, branch=BRANCH
    )
    snap = fetch_store_gate_status(
        target, env=ENV, opener=StatusOpener(statuses=[])
    )
    assert snap.missing is True
    assert snap.pending is True
    assert snap.is_12_of_12 is False


def test_wait_timeout_fail_closed():
    target = BuildsTarget(
        owner="bopoadz-del", repo="cerebrum-builds", sha=SHA, branch=BRANCH
    )
    snap = wait_for_store_gate(
        target,
        env=ENV,
        opener=StatusOpener(state="pending", description=""),
        clock=lambda: 0.0,
        sleep=lambda _s: None,
        poll_s=0.0,
        wall_s=0.0,
    )
    assert snap.timeout is True
    assert snap.is_12_of_12 is False


def test_ingest_success_unlocks_package_without_writer(tmp_path):
    out = _handoff_ledger(tmp_path)
    result = ingest_n3_store_gate(
        out, wait=False, env=ENV, opener=StatusOpener()
    )
    assert result.ok is True
    assert result.honesty == N3_STORE_GATE_GREEN
    assert handoff_awaiting_n3(out) is False
    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is True
    assert status["honesty"] == N3_STORE_GATE_GREEN
    assert status["cycle"] == "pilot"
    assert "STORE PASS" in status["detail"]
    assert is_build_complete(out) is True
    report = status.get("acceptance") or {}
    assert report.get("ok") is True
    assert report.get("passed") == ACCEPTANCE_REQUIRED
    from app.factory.build.store_acceptance import (
        acceptance_export_blocker,
        read_acceptance_report,
        workspace_acceptance_is_kk,
    )

    assert workspace_acceptance_is_kk(out) is True
    assert acceptance_is_kk(read_acceptance_report(out)) is True
    assert acceptance_export_blocker(status, out) is None
    from app.factory.build.authorship import thin_store_green_export_blocker

    assert thin_store_green_export_blocker(status, out) is None


def test_ingest_red_gate_fail_closed(tmp_path):
    out = _handoff_ledger(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=False,
        env=ENV,
        opener=StatusOpener(
            state="failure", description="acceptance.py in Docker 0/12"
        ),
    )
    assert result.ok is False
    assert result.honesty == N3_STORE_GATE_FAILED
    assert handoff_awaiting_n3(out) is False
    status = build_status(out)
    assert status["state"] == "failed"
    assert status["pilot_ready"] is False
    assert is_build_complete(out) is False


def test_ingest_timeout_fail_closed(tmp_path):
    out = _handoff_ledger(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=True,
        env=ENV,
        opener=StatusOpener(state="pending", description=""),
        clock=lambda: 0.0,
        sleep=lambda _s: None,
        poll_s=0.0,
        wall_s=0.0,
    )
    assert result.ok is False
    assert result.honesty == N3_STORE_GATE_TIMEOUT
    status = build_status(out)
    assert status["state"] == "failed"
    assert status["honesty"] == N3_STORE_GATE_TIMEOUT


def test_ingest_missing_token_fail_closed(tmp_path, monkeypatch):
    out = _handoff_ledger(tmp_path)
    monkeypatch.delenv("CEREBRUM_BUILDS_GITHUB_TOKEN", raising=False)
    result = ingest_n3_store_gate(out, wait=False, env={})
    assert result.ok is False
    assert result.honesty == N3_STORE_GATE_MISSING


def test_continue_ingests_and_does_not_start_writer(tmp_path, monkeypatch):
    out = _handoff_ledger(tmp_path)
    state = _session_for(out)
    called = []
    real_ingest = ingest_n3_store_gate

    def boom(*_a, **_k):
        called.append("generate")
        raise AssertionError("generate_product must not run on HANDOFF_TO_N3")

    def ingest_live(output_dir, wait=False, **kwargs):
        called.append("ingest")
        assert wait is False
        return real_ingest(
            output_dir, wait=False, env=ENV, opener=StatusOpener()
        )

    monkeypatch.setattr(platform_chat_flow, "generate_product", boom)
    monkeypatch.setattr(
        "app.factory.build.n3_store_gate.ingest_n3_store_gate", ingest_live
    )
    result = platform_chat_flow.start_or_resume_coder(state)
    assert called == ["ingest"]
    assert result.get("n3_ingest") is True
    assert "Background Agent" in (result.get("summary") or "")
    assert "WRITER" in (result.get("summary") or "") or "coding agent" in (
        result.get("summary") or ""
    ).lower()
    assert result["n3"]["ok"] is True
    assert Path(state.product_design.generation["output_dir"]) == out
    assert not any("generate" == item for item in called)


def test_start_fresh_does_not_open_new_workspace_on_handoff(tmp_path, monkeypatch):
    out = _handoff_ledger(tmp_path)
    state = _session_for(out)
    called = []
    real_ingest = ingest_n3_store_gate

    def boom(*_a, **_k):
        called.append("generate")
        raise AssertionError("fresh generate must not run on HANDOFF_TO_N3")

    def ingest_live(output_dir, wait=False, **kwargs):
        called.append("ingest")
        return real_ingest(
            output_dir, wait=False, env=ENV, opener=StatusOpener()
        )

    monkeypatch.setattr(platform_chat_flow, "generate_product", boom)
    monkeypatch.setattr(
        "app.factory.build.n3_store_gate.ingest_n3_store_gate", ingest_live
    )
    result = platform_chat_flow.start_fresh_generation(state)
    assert called == ["ingest"]
    assert result.get("fresh") is not True
    assert result.get("n3_ingest") is True


def test_start_runner_build_does_not_fresh_workspace_on_handoff(tmp_path, monkeypatch):
    from app.factory.blueprint import load_blueprint
    from app.factory.build_jobs import start_runner_build

    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    out = _handoff_ledger(tmp_path)
    started = []

    def fake_start(output_dir, **_k):
        started.append(str(output_dir))
        return True

    monkeypatch.setattr(
        "app.factory.build.n3_store_gate.start_n3_ingest_job", fake_start
    )
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready",
        lambda: None,
    )
    result = start_runner_build(bp, out)
    assert result.get("n3_ingest") is True
    assert result["output_dir"] == str(out)
    assert started == [str(out)]
    assert "__run" not in Path(result["output_dir"]).name


def test_report_maps_n3_floor_aliases():
    raw = {
        "ok": True,
        "passed": 12,
        "total": 12,
        "score": "12/12",
        "lines": [
            {"name": "no_token_401", "status": "PASS", "detail": ""},
            {"name": "missing_field_422", "status": "PASS", "detail": ""},
            {"name": "enum_422", "status": "PASS", "detail": ""},
            {"name": "ui_served_200", "status": "PASS", "detail": ""},
            {"name": "rag_roundtrip_hit", "status": "SKIP", "detail": "no-rag"},
            {"name": "single_persistence_root", "status": "PASS", "detail": ""},
            {"name": "ci_present_full_suite", "status": "PASS", "detail": ""},
            {"name": "handler_bodies_distinct", "status": "PASS", "detail": ""},
            {"name": "health_fail_closed", "status": "PASS", "detail": ""},
            {"name": "openapi_committed", "status": "PASS", "detail": ""},
            {"name": "docker_health_200", "status": "PASS", "detail": ""},
            {"name": "authorship==receipt", "status": "PASS", "detail": ""},
        ],
    }
    report = report_from_store_gate_payload(raw)
    names = [line.name for line in report.lines]
    assert "ci_present_and_full_suite" in names
    assert "authorship_floor" in names
    assert report.ok is True
    assert acceptance_is_kk(report) is True
