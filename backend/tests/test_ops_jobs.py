"""scripts/ops.py: the live-API ops jobs that run on Actions (ops.yml).

Each job fails closed with one readable reason when it cannot answer -- an
API that cannot be reached, a refused smoke gate, a target no roster account
owns -- and nothing it writes may carry a secret.
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_PATH = REPO_ROOT / "scripts" / "ops.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ops.yml"


def _load():
    spec = importlib.util.spec_from_file_location("ops_under_test", OPS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ops(monkeypatch):
    mod = _load()
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "gate-value")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    return mod


class FakeApi:
    """A live API in miniature: a roster of accounts, each owning sessions."""

    def __init__(self, owned, *, health=200, gate=200, build=None, package=None):
        self.owned = owned  # account index -> {session_id: state}
        self.health = health
        self.gate = gate
        self.build = build or {"state": "failed"}
        self.package = package
        self.calls = []

    def __call__(self, method, path, body=None, *, token=None, base="", raw=False, headers=None, **_kw):
        self.calls.append((method, path, token))
        if self.health == 0:
            return 0, {"detail": "URLError: [Errno -2] Name or service not known"}
        if path == "/health":
            return self.health, {"status": "ok" if self.health == 200 else "down"}
        if path == "/version":
            return 200, {"git_sha": "abc1234"}
        if path == "/v1/auth/smoke-login":
            if self.gate != 200:
                return self.gate, {"detail": "Invalid or missing smoke gate token"}
            asked = (body or {}).get("principals")
            size = len(self.owned)
            if not isinstance(asked, int) or not 1 <= asked <= size:
                return 400, {"detail": f"principals must be an integer 1..{size} (the declared smoke roster); got {asked!r}"}
            return 200, {"login_tokens": [f"tok{i}" for i in range(asked)]}
        index = int(token[3:]) if token and token.startswith("tok") else -1
        mine = self.owned.get(index, {})
        if path == "/v1/sessions/":
            return 200, [{"session_id": sid} for sid in mine]
        parts = path.split("/")
        sid = parts[3] if len(parts) > 3 else ""
        if sid not in mine:
            return 404, {"detail": "Session not found"}
        tail = "/".join(parts[4:])
        if tail == "":
            return 200, mine[sid]
        if tail == "product/build-status":
            return 200, {"ok": True, "build": self.build}
        if tail == "product/ledger":
            return 404, {"detail": "Not Found"}
        if tail == "product/package":
            return (200, self.package) if self.package else (409, {"detail": "not certified"})
        if tail == "product":
            return 200, {"generation": mine[sid]["product_design"].get("generation")}
        return 404, {"detail": "no such route"}


def _state(sid, platform):
    return {
        "session_id": sid,
        "product_design": {
            "platform_id": platform,
            "generation": {"product_id": "p", "output_dir": f"/data/out/{sid}"},
            "last_error": None,
        },
    }


def _error(out: Path, job: str) -> str:
    return json.loads((out / f"error_{job}.json").read_text())["error"]


def test_unreachable_api_fails_closed_with_the_reason(ops, tmp_path):
    api = FakeApi({0: {}}, health=0)
    rc = ops.main(["ledger-dump", "--target", "sess_x", "--out", str(tmp_path)], req=api)
    assert rc == 2
    reason = _error(tmp_path, "ledger-dump")
    assert "API unreachable" in reason and "Name or service not known" in reason


@pytest.mark.parametrize("job", ["ledger-dump", "gate-dispatch", "export-check"])
def test_every_job_fails_closed_when_the_api_is_unreachable(ops, tmp_path, job):
    rc = ops.main([job, "--target", "sess_x", "--out", str(tmp_path)], req=FakeApi({0: {}}, health=0))
    assert rc == 2
    assert "API unreachable" in _error(tmp_path, job)


def test_unhealthy_api_is_not_an_answer(ops, tmp_path):
    rc = ops.main(["ledger-dump", "--target", "sess_x", "--out", str(tmp_path)], req=FakeApi({0: {}}, health=503))
    assert rc == 2
    assert "not healthy" in _error(tmp_path, "ledger-dump")


def test_a_refused_smoke_gate_fails_closed(ops, tmp_path):
    rc = ops.main(["ledger-dump", "--target", "sess_x", "--out", str(tmp_path)], req=FakeApi({0: {}}, gate=401))
    assert rc == 2
    assert "smoke gate refused" in _error(tmp_path, "ledger-dump")


def test_no_gate_token_fails_closed(ops, tmp_path, monkeypatch):
    monkeypatch.delenv("SMOKE_GATE_TOKEN")
    rc = ops.main(["ledger-dump", "--target", "sess_x", "--out", str(tmp_path)], req=FakeApi({0: {}}))
    assert rc == 2
    assert "SMOKE_GATE_TOKEN is not set" in _error(tmp_path, "ledger-dump")


def test_a_target_no_roster_account_owns_fails_closed(ops, tmp_path):
    api = FakeApi({0: {"sess_a": _state("sess_a", "plt_a")}, 1: {}})
    rc = ops.main(["ledger-dump", "--target", "sess_nobody", "--out", str(tmp_path)], req=api)
    assert rc == 2
    assert "no smoke-roster account (2 checked)" in _error(tmp_path, "ledger-dump")


def test_the_roster_size_is_read_from_the_server(ops, tmp_path):
    owned = {i: {} for i in range(4)}
    owned[3] = {"sess_d": _state("sess_d", "plt_d")}
    api = FakeApi(owned)
    assert ops.main(["ledger-dump", "--target", "sess_d", "--out", str(tmp_path)], req=api) == 0
    asks = [c for c in api.calls if c[1] == "/v1/auth/smoke-login"]
    assert len(asks) == 2
    record = json.loads((tmp_path / "ledger_sess_d.json").read_text())
    assert record["account_index"] == 3


def test_ledger_dump_finds_the_owner_by_session_id(ops, tmp_path):
    build = {
        "state": "failed",
        "phase_trail": [{"phase": "WRITER", "outcome": "FAILED"}],
        "failure": {"gate": "WRITER", "check": "writer_contract", "detail": "the check said so"},
        "decisions": [{"gate": "WRITER", "class": "product", "round": "1/2"}],
        "activity_log": [{"ts": "t", "role": "WRITER", "text": "STEP 1"}],
    }
    api = FakeApi({0: {}, 1: {"sess_b": _state("sess_b", "plt_b")}}, build=build)
    assert ops.main(["ledger-dump", "--target", "sess_b", "--out", str(tmp_path)], req=api) == 0
    record = json.loads((tmp_path / "ledger_sess_b.json").read_text())
    assert record["account_index"] == 1
    assert record["build_status"]["failure"]["check"] == "writer_contract"
    assert record["ledger"] is None and record["ledger_http"] == 404
    assert record["service_log"]["ok"] is False and record["service_log"]["reason"]
    summary = (tmp_path / "ledger_sess_b.md").read_text()
    assert "writer_contract" in summary and "STEP 1" in summary
    # Tokens never reach what the job writes.
    assert "tok1" not in json.dumps(record) and "tok1" not in summary


def test_a_platform_id_prefix_finds_its_session(ops, tmp_path):
    api = FakeApi({0: {"sess_a": _state("sess_a", "plt_4b0531250000")}, 1: {"sess_b": _state("sess_b", "plt_ffff")}})
    assert ops.main(["ledger-dump", "--target", "plt_4b053125", "--out", str(tmp_path)], req=api) == 0
    assert (tmp_path / "ledger_sess_a.json").is_file()
    assert not (tmp_path / "ledger_sess_b.json").exists()


def test_a_run_name_resolves_through_the_cycles_repros(ops, tmp_path):
    repros = tmp_path / "repros.json"
    repros.write_text(json.dumps({"repros": {"some_run": {"session_id": "sess_c"}}}))
    api = FakeApi({0: {}, 1: {}, 2: {"sess_c": _state("sess_c", "plt_c")}})
    out = tmp_path / "out"
    assert ops.main(["ledger-dump", "--target", "some_run", "--repros", str(repros), "--out", str(out)], req=api) == 0
    assert (out / "ledger_sess_c.json").is_file()


def test_gate_dispatch_needs_a_branch_or_a_target(ops, tmp_path):
    assert ops.main(["gate-dispatch", "--out", str(tmp_path)], req=FakeApi({0: {}})) == 2
    assert "needs --branch" in _error(tmp_path, "gate-dispatch")


def test_gate_dispatch_without_a_builds_token_fails_closed(ops, tmp_path, monkeypatch):
    monkeypatch.delenv("BUILDS_GITHUB_TOKEN", raising=False)
    assert ops.main(["gate-dispatch", "--branch", "build/x", "--out", str(tmp_path)], req=FakeApi({0: {}})) == 2
    assert "BUILDS_GITHUB_TOKEN is not set" in _error(tmp_path, "gate-dispatch")


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return buf.getvalue()


def test_export_check_audits_the_zip_and_your_platforms(ops, tmp_path, monkeypatch):
    blob = _zip(["app/main.py", "README.md"])
    api = FakeApi({0: {"sess_e": _state("sess_e", "plt_e")}}, package=blob)
    monkeypatch.setattr(ops, "_export_rule", lambda: (lambda p: "__pycache__" not in p.parts))
    assert ops.main(["export-check", "--target", "sess_e", "--out", str(tmp_path)], req=api) == 0
    record = json.loads((tmp_path / "export_sess_e.json").read_text())
    assert record["strip"] == {"files": 2, "bytes": len(blob), "leaked": [], "ok": True}
    assert record["your_platforms"]["listed"] is True and record["your_platforms"]["has_generation"]


def test_export_check_fails_on_a_leaked_file(ops, tmp_path, monkeypatch):
    blob = _zip(["app/main.py", "app/__pycache__/main.cpython-311.pyc"])
    api = FakeApi({0: {"sess_e": _state("sess_e", "plt_e")}}, package=blob)
    monkeypatch.setattr(ops, "_export_rule", lambda: (lambda p: "__pycache__" not in p.parts))
    assert ops.main(["export-check", "--target", "sess_e", "--out", str(tmp_path)], req=api) == 1
    record = json.loads((tmp_path / "export_sess_e.json").read_text())
    assert record["strip"]["leaked"] == ["app/__pycache__/main.cpython-311.pyc"]


def test_export_check_reads_the_real_export_rule():
    rule = _load()._export_rule()
    assert rule(Path("app/main.py")) is True
    assert rule(Path("app/__pycache__/main.cpython-311.pyc")) is False


def test_export_check_on_an_uncertified_platform_says_why(ops, tmp_path, monkeypatch):
    api = FakeApi({0: {"sess_e": _state("sess_e", "plt_e")}})
    monkeypatch.setattr(ops, "_export_rule", lambda: (lambda p: True))
    assert ops.main(["export-check", "--target", "sess_e", "--out", str(tmp_path)], req=api) == 1
    record = json.loads((tmp_path / "export_sess_e.json").read_text())
    assert record["package_http"] == 409 and "not certified" in record["detail"]


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop123",
        "key sk-abcdefghijklmnopqrstu",
        "ghp_" + "a" * 36,
        "AKIA" + "B" * 16,
        "password=hunter2hunter2",
        "postgres://user:pa55word@db.internal/x",
    ],
)
def test_secret_shapes_are_redacted(ops, text):
    assert "[redacted]" in ops.redact(text)


def test_the_workflow_takes_its_targets_as_inputs():
    wf = yaml.safe_load(WORKFLOW_PATH.read_text())
    on = wf.get(True) or wf.get("on")
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"action", "target", "branch", "run_id", "sha"}
    assert inputs["action"]["options"] == ["ledger-dump", "gate-dispatch", "export-check"]
    assert on["push"]["branches"] == ["ops-run/**"]
    steps = wf["jobs"]["ops"]["steps"]
    run = next(s for s in steps if s.get("name") == "Run")
    assert run["env"]["SMOKE_GATE_TOKEN"] == "${{ secrets.SMOKE_GATE_TOKEN }}"
    committed = next(s for s in steps if s.get("name") == "Commit the answer to ops-results")
    assert committed["if"] == "always()"
