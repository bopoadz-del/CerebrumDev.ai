"""The deploy smoke can see a coding agent sharing the server's session.

Live 2026-10-07 22:03 and 2026-10-08 03:19 UTC: a WRITER CLI inherited the
uvicorn process group, a group-wide kill from its shell stopped the server,
and every in-flight build died. #700 put every agent in its own session; this
is the regression guard: the server reports (smoke-gated, counts only) whether
any live agent shares its session and whether the spawn path still asks for a
new one, and the post-deploy smoke reads it while its own writer runs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.factory.build import agent_process

GATE = {"X-Smoke-Gate": "correct-gate-token"}
SERVER_SID = 4242


class _Proc:
    def __init__(self, pid: int, alive: bool = True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


@pytest.fixture(autouse=True)
def _empty_roster():
    agent_process._LIVE.clear()
    yield
    agent_process._LIVE.clear()


@pytest.fixture()
def posix_sessions(monkeypatch):
    """Platform-independent session table: pid 0 is the server."""
    table = {0: SERVER_SID}
    monkeypatch.setattr(agent_process, "_posix", lambda: True)
    monkeypatch.setattr(agent_process, "_session_of", lambda pid: table.get(pid))
    return table


def test_no_agent_running_still_proves_the_spawn_path(posix_sessions):
    report = agent_process.process_isolation_report()
    assert report["spawn_isolated"] is True
    assert report["live_agents"] == 0
    assert report["isolated"] is True


def test_an_agent_in_its_own_session_is_isolated(posix_sessions):
    posix_sessions[101] = 101  # setsid: the agent leads its own session
    agent_process.track_agent(_Proc(101))
    report = agent_process.process_isolation_report()
    assert report["live_agents"] == 1
    assert report["agents_in_server_session"] == 0
    assert report["isolated"] is True


def test_an_agent_in_the_servers_session_is_caught(posix_sessions):
    # The pre-#700 shape: the child inherited the server's session.
    posix_sessions[102] = SERVER_SID
    agent_process.track_agent(_Proc(102))
    report = agent_process.process_isolation_report()
    assert report["agents_in_server_session"] == 1
    assert report["isolated"] is False


def test_a_spawn_path_that_stops_asking_for_a_new_session_is_caught(posix_sessions, monkeypatch):
    monkeypatch.setattr(agent_process, "agent_popen_kwargs", lambda: {})
    report = agent_process.process_isolation_report()
    assert report["spawn_isolated"] is False
    assert report["isolated"] is False


def test_an_exited_agent_drops_off_the_roster(posix_sessions):
    posix_sessions[103] = SERVER_SID
    agent_process.track_agent(_Proc(103, alive=False))
    report = agent_process.process_isolation_report()
    assert report["live_agents"] == 0
    assert report["isolated"] is True


def test_the_report_carries_no_pid_or_session_id(posix_sessions):
    posix_sessions[104] = 104
    agent_process.track_agent(_Proc(104))
    report = agent_process.process_isolation_report()
    assert set(report) == {
        "posix", "spawn_isolated", "live_agents", "agents_in_server_session", "isolated",
    }
    assert SERVER_SID not in report.values() and 104 not in report.values()


def test_both_agent_spawn_sites_register_their_process():
    import inspect

    from app.factory.build import codewhale_worker, coder_session

    assert "track_agent(proc)" in inspect.getsource(codewhale_worker.run_worker_job)
    assert "track_agent(subprocess.Popen(" in inspect.getsource(coder_session._run_cli_session)


# --- the endpoint -------------------------------------------------------------


def _client(monkeypatch, token: str | None) -> TestClient:
    if token is None:
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SMOKE_GATE_TOKEN", token)
    from app.routers import accounts

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    return TestClient(app)


def test_the_endpoint_is_hidden_without_a_configured_gate(monkeypatch):
    res = _client(monkeypatch, None).get("/v1/auth/smoke-process-isolation", headers=GATE)
    assert res.status_code == 404


def test_the_endpoint_refuses_a_wrong_gate(monkeypatch):
    res = _client(monkeypatch, "correct-gate-token").get(
        "/v1/auth/smoke-process-isolation", headers={"X-Smoke-Gate": "nope"}
    )
    assert res.status_code == 401


def test_the_endpoint_answers_the_gate_with_the_report(monkeypatch, posix_sessions):
    posix_sessions[105] = SERVER_SID
    agent_process.track_agent(_Proc(105))
    res = _client(monkeypatch, "correct-gate-token").get(
        "/v1/auth/smoke-process-isolation", headers=GATE
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["isolated"] is False
    assert body["agents_in_server_session"] == 1


# --- the smoke side -------------------------------------------------------------


def _smoke():
    path = Path(__file__).resolve().parents[3] / "scripts" / "post_deploy_smoke.py"
    spec = importlib.util.spec_from_file_location("post_deploy_smoke_isolation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAILURES.clear()
    return module


def _answers(module, monkeypatch, answers):
    calls = []

    def fake_req(method, path, *a, **kw):
        calls.append((path, kw.get("extra_headers")))
        return answers.pop(0) if answers else (200, {"isolated": True, "live_agents": 0,
                                                     "agents_in_server_session": 0,
                                                     "spawn_isolated": True})

    monkeypatch.setattr(module, "req", fake_req)
    return calls


def test_the_smoke_reads_dead_when_an_agent_shares_the_server_session(monkeypatch, capsys):
    smoke = _smoke()
    calls = _answers(smoke, monkeypatch, [
        (200, {"isolated": True, "live_agents": 1, "agents_in_server_session": 0, "spawn_isolated": True}),
        (200, {"isolated": False, "live_agents": 1, "agents_in_server_session": 1, "spawn_isolated": True}),
    ])
    clock = iter([0, 61, 122]).__next__
    watch = smoke.ProcessIsolationWatch("gate", clock=clock)
    watch.sample()
    watch.sample()
    watch.record()
    out = capsys.readouterr().out
    assert "[DEAD] agent shell isolated from the server" in out
    assert "agents_in_server_session=1" in out
    assert smoke.FAILURES == ["agent shell isolated from the server"]
    assert all(h == {"X-Smoke-Gate": "gate"} for _, h in calls)


def test_the_smoke_reads_live_when_every_sample_is_isolated(monkeypatch, capsys):
    smoke = _smoke()
    _answers(smoke, monkeypatch, [
        (200, {"isolated": True, "live_agents": 1, "agents_in_server_session": 0, "spawn_isolated": True}),
    ])
    watch = smoke.ProcessIsolationWatch("gate", clock=iter([0]).__next__)
    watch.sample()
    watch.record()
    assert "[LIVE] agent shell isolated from the server" in capsys.readouterr().out
    assert smoke.FAILURES == []


def test_the_smoke_samples_at_most_once_a_minute(monkeypatch):
    smoke = _smoke()
    calls = _answers(smoke, monkeypatch, [])
    times = iter([0, 5, 30, 59, 60, 61]).__next__
    watch = smoke.ProcessIsolationWatch("gate", clock=times)
    for _ in range(6):
        watch.sample()
    assert len(calls) == 2  # t=0 and t=60


def test_the_export_wait_samples_while_the_build_runs(monkeypatch):
    smoke = _smoke()
    seen = []
    replies = [(409, b"{}"), (200, {"build": {"state": "building"}}),
               (200, b"PK..."), (200, {"build": {"state": "succeeded"}})]
    monkeypatch.setattr(smoke, "req", lambda *a, **kw: replies.pop(0))
    smoke.wait_for_export("s", "t", wait_s=100, sleep=lambda _s: None,
                          clock=iter(range(0, 1000, 10)).__next__,
                          observe=lambda: seen.append(1))
    assert seen == [1]


def test_the_smoke_without_a_gate_skips_without_failing(monkeypatch, capsys):
    smoke = _smoke()
    calls = _answers(smoke, monkeypatch, [])
    watch = smoke.ProcessIsolationWatch("")
    watch.sample()
    watch.record()
    assert calls == []
    assert "[SKIP] agent shell isolated from the server" in capsys.readouterr().out
    assert smoke.FAILURES == []
