"""The release cycle: smoke A and the repro builds run AT THE SAME TIME, each
on its own verified account; smoke B runs after all of them; one report.

Owner's standing rule (2026-10-08): repro builds run in parallel with smoke A
on separate accounts, the smoke keeps its reserved slot, slots queue and never
fail, cycle target <= 1.25 h. Serial runs where parallel is available are
forbidden -- on 2026-10-07 every cycle ran vineyard AFTER smoke A and cost an
hour each time.
"""

from __future__ import annotations

import importlib.util
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CYCLE_PATH = REPO_ROOT / "scripts" / "release_cycle.py"
CONFIG_PATH = REPO_ROOT / "scripts" / "release_cycle.json"
SMOKE_PATH = REPO_ROOT / "scripts" / "post_deploy_smoke.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cycle():
    return _load("release_cycle", CYCLE_PATH)


# -- account assignment ------------------------------------------------------


def test_the_smoke_keeps_index_zero_and_every_repro_gets_its_own_account(cycle):
    plan = cycle.assign_accounts(["one", "two"], available=3)
    assert plan["smoke"] == cycle.SMOKE_ACCOUNT_INDEX == 0
    assert plan["repros"] == {"one": 1, "two": 2}
    used = [plan["smoke"], *plan["repros"].values()]
    assert len(used) == len(set(used)), "two runs on one account share its slot"


def test_a_short_roster_fails_closed_instead_of_doubling_up(cycle):
    with pytest.raises(cycle.CycleError) as exc:
        cycle.assign_accounts(["one", "two"], available=2)
    assert "3" in str(exc.value) and "2" in str(exc.value)


def test_repro_names_are_distinct(cycle):
    with pytest.raises(cycle.CycleError):
        cycle.assign_accounts(["one", "one"], available=5)


# -- concurrency -------------------------------------------------------------


def test_runs_start_together_not_one_after_another(cycle):
    # Each run waits at a barrier for ALL the others. Run serially, the first
    # one would wait alone and the barrier would time out.
    barrier = threading.Barrier(3, timeout=10)

    def run():
        barrier.wait()
        return {"status": "exported"}

    results = cycle.run_parallel({"smoke_a": run, "one": run, "two": run})
    assert {k: v["status"] for k, v in results.items()} == {
        "smoke_a": "exported", "one": "exported", "two": "exported",
    }


def test_one_run_failing_never_cancels_the_others(cycle):
    finished = threading.Event()

    def boom():
        raise RuntimeError("writer died")

    def slow():
        time.sleep(0.2)
        finished.set()
        return {"status": "exported"}

    results = cycle.run_parallel({"boom": boom, "slow": slow})
    assert finished.is_set()
    assert results["slow"]["status"] == "exported"
    assert results["boom"]["status"] == "error"
    assert "writer died" in results["boom"]["error"]


# -- driving one build -------------------------------------------------------


def _zip_with_manifest(manifest, extra=3):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest))
        for i in range(extra):
            zf.writestr(f"app/f{i}.py", "x = 1\n")
    return buf.getvalue()


class _FakeSmoke:
    """The smoke client's surface the cycle drives, scripted."""

    TRANSIENT = {502, 503, 504}

    def __init__(self, package_answers, states):
        self.package_answers = list(package_answers)
        self.states = list(states)
        self.calls = []

    def req(self, method, path, body=None, token=None, raw=False, **kw):
        self.calls.append((method, path, token))
        if path == "/v1/sessions/":
            return 200, {"session_id": "sess_x"}
        if path.endswith("/product/package"):
            return self.package_answers.pop(0)
        if path.endswith("/product/build-status"):
            state = self.states.pop(0) if self.states else "building"
            return 200, {"build": {"state": state, "phases_done": 2, "phases_total": 5}}
        return 200, {}

    def chat_until_drafted(self, sid, tok, brief):
        return "blueprint", 1

    def chat(self, sid, tok, msg, action=None, value=None, **kw):
        return "generation"


def test_a_gateway_timeout_while_polling_is_not_the_builds_verdict(cycle):
    """Live 2026-10-08: a 504 on /product/package during a platform restart
    ended the repro as 'exported False' while the build itself was still
    running. A transient answer is a reason to keep polling, never a verdict."""
    blob = _zip_with_manifest(
        {"certified": True, "build_level": {"build_level": "production"}, "advisory_checks": []}
    )
    smoke = _FakeSmoke(
        package_answers=[(409, b"{}"), (504, b"bad gateway"), (409, b"{}"), (200, blob)],
        states=["building", "building", "building", "succeeded"],
    )
    out = cycle.drive_build(
        smoke, "tok-1", "a brief", level="production", wait_s=60, poll_s=0,
    )
    assert out["status"] == "exported", out
    assert out["package_http"] == 200
    assert out["export"]["bytes"] == len(blob)
    assert out["export"]["manifest"]["certified"] is True
    assert all(token == "tok-1" for _m, _p, token in smoke.calls)


def test_a_failed_build_is_reported_with_the_servers_reason(cycle):
    smoke = _FakeSmoke(
        package_answers=[(409, json.dumps({"detail": "gate X failed"}).encode())],
        states=["failed"],
    )
    out = cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=60, poll_s=0)
    assert out["status"] == "failed"
    assert "gate X failed" in out["detail"]


# -- the export summary and the report ---------------------------------------


def test_the_export_summary_reads_the_manifest_inside_the_zip(cycle):
    blob = _zip_with_manifest(
        {
            "certified": True,
            "build_level": {"build_level": "production", "stop_gate": "STORE"},
            "advisory_checks": [{"check": "c", "reason": "r"}],
        },
        extra=4,
    )
    summary = cycle.summarize_export(blob)
    assert summary == {
        "bytes": len(blob),
        "files": 5,
        "manifest": {
            "certified": True,
            "build_level": {"build_level": "production", "stop_gate": "STORE"},
            "advisory_checks": [{"check": "c", "reason": "r"}],
        },
    }


def _repro(status="exported", certified=True):
    return {
        "status": status,
        "account_index": 1,
        "session_id": "sess_x",
        "package_http": 200 if status == "exported" else 409,
        "export": {"bytes": 10, "files": 6, "manifest": {"certified": certified}} if status == "exported" else None,
        "wall_s": 100.0,
    }


def test_the_report_carries_every_run_and_the_cycle_time(cycle):
    report = cycle.build_report(
        smoke_a={"status": "pass", "wall_s": 10.0},
        repros={"one": _repro(), "two": _repro()},
        smoke_b={"status": "pass", "wall_s": 12.0},
        started_at=1000.0,
        finished_at=1000.0 + 3600.0,
        target_s=4500,
        commit="abc123",
    )
    assert report["verdict"] == "pass"
    assert report["commit"] == "abc123"
    assert report["cycle_s"] == 3600.0
    assert report["within_target"] is True
    assert set(report["runs"]) == {"smoke_a", "one", "two", "smoke_b"}
    assert report["runs"]["one"]["export"]["manifest"]["certified"] is True


def test_an_uncertified_export_or_a_red_smoke_fails_the_cycle(cycle):
    common = dict(started_at=0.0, finished_at=100.0, target_s=4500, commit="c")
    assert cycle.build_report(
        smoke_a={"status": "pass"}, smoke_b={"status": "pass"},
        repros={"one": _repro(certified=False)}, **common,
    )["verdict"] == "fail"
    assert cycle.build_report(
        smoke_a={"status": "fail"}, smoke_b={"status": "pass"},
        repros={"one": _repro()}, **common,
    )["verdict"] == "fail"
    assert cycle.build_report(
        smoke_a={"status": "pass"}, smoke_b={"status": "pass"},
        repros={"one": _repro(status="failed")}, **common,
    )["verdict"] == "fail"
    late = cycle.build_report(
        smoke_a={"status": "pass"}, smoke_b={"status": "pass"},
        repros={"one": _repro()}, started_at=0.0, finished_at=5000.0,
        target_s=4500, commit="c",
    )
    # Over target is reported, not hidden -- and not a product failure.
    assert late["verdict"] == "pass" and late["within_target"] is False


# -- the briefs are data -----------------------------------------------------


def test_the_briefs_come_from_the_config_file_not_the_code(cycle):
    config = cycle.load_config(CONFIG_PATH)
    names = [r["name"] for r in config["repros"]]
    assert len(names) == len(set(names)) >= 1
    source = CYCLE_PATH.read_text(encoding="utf-8")
    for repro in config["repros"]:
        assert repro["brief"] not in source


def test_a_config_entry_without_a_brief_is_refused(cycle, tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps({"repros": [{"name": "x"}]}), encoding="utf-8")
    with pytest.raises(cycle.CycleError):
        cycle.load_config(bad)


# -- the roster client -------------------------------------------------------


def test_the_roster_is_asked_of_the_server_by_count(monkeypatch):
    smoke = _load("post_deploy_smoke", SMOKE_PATH)
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "gate")
    seen = {}

    def req(method, path, body=None, **kw):
        seen["body"] = body
        return 200, {"login_tokens": ["t0", "t1", "t2"], "login_token": "t0"}

    smoke.req = req
    assert smoke.verified_token_roster(3) == ["t0", "t1", "t2"]
    assert seen["body"] == {"principals": 3}


def test_an_older_server_yields_its_pair_and_the_cycle_sees_it_is_short(monkeypatch):
    smoke = _load("post_deploy_smoke", SMOKE_PATH)
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "gate")
    smoke.req = lambda *a, **k: (200, {"login_token": "t0", "login_token_b": "t1"})
    assert smoke.verified_token_roster(3) == ["t0", "t1"]


# -- the workflow ------------------------------------------------------------


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_the_repros_start_beside_smoke_a_and_smoke_b_waits_for_both():
    jobs = _workflow()["jobs"]
    assert "live-smoke" in jobs and "release-repros" in jobs and "smoke-b" in jobs
    # No `needs`: the repro job starts on the same deploy event as smoke A.
    assert not jobs["release-repros"].get("needs")
    assert not jobs["live-smoke"].get("needs")
    needs = jobs["smoke-b"]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert set(needs) == {"live-smoke", "release-repros"}


def test_the_cycle_reads_only_secrets_that_already_exist():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    import re

    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    assert used <= {"SMOKE_GATE_TOKEN", "SMOKE_EMAIL", "SMOKE_PASSWORD", "GITHUB_TOKEN"}, used


def test_the_workflow_runs_the_cycle_script_for_the_repros_and_the_report():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/release_cycle.py repros" in text
    assert "scripts/release_cycle.py report" in text
