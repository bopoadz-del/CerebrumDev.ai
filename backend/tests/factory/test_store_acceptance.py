"""Store-green is scripts/acceptance.py k/k — not authorship, not ok:true."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    ACCEPTANCE_SCRIPT_REL,
    AcceptanceLine,
    AcceptanceReport,
    acceptance_export_blocker,
    acceptance_is_kk,
    acceptance_surface_incomplete,
    gate_store_acceptance,
    parse_acceptance_output,
    read_acceptance_report,
    render_acceptance_script,
    render_auth_module,
    render_github_ci,
    render_openapi,
    stamp_acceptance_into_path,
    workspace_acceptance_is_kk,
    write_acceptance_report,
)


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _kk_output() -> str:
    lines = [f"PASS {name} — ok" for name in ACCEPTANCE_CHECK_NAMES]
    lines[-1] = "PASS authorship_floor — need≥5"
    lines.append(f"ACCEPTANCE: {ACCEPTANCE_REQUIRED}/{ACCEPTANCE_REQUIRED}")
    return "\n".join(lines) + "\n"


def test_harness_defines_twelve_named_checks_authorship_last():
    assert len(ACCEPTANCE_CHECK_NAMES) >= 13
    assert ACCEPTANCE_CHECK_NAMES[-1] == "authorship_floor"
    assert "no_token_401" in ACCEPTANCE_CHECK_NAMES
    script = render_acceptance_script()
    for name in ACCEPTANCE_CHECK_NAMES:
        assert name in script
    assert "ok:false" in script.lower() or "ok: false" in script or "ok:%s" in script
    assert "STORE_DOCKER_HEALTH" in script
    compile(script, "acceptance.py", "exec")


def test_parse_counts_skip_as_satisfied_but_fail_is_not_kk():
    text = "\n".join(
        [
            # A line per check the floor holds, not a transcript typed out
            # when it held thirteen. The one SKIP is what this test is about.
            *(
                "SKIP rag_roundtrip_hit — no-rag-surface"
                if name == "rag_roundtrip_hit"
                else f"PASS {name} — measured"
                for name in ACCEPTANCE_CHECK_NAMES
            ),
            f"ACCEPTANCE: {len(ACCEPTANCE_CHECK_NAMES)}/{len(ACCEPTANCE_CHECK_NAMES)}",
        ]
    )
    report = parse_acceptance_output(text)
    assert report.passed == len(ACCEPTANCE_CHECK_NAMES)
    assert report.ok is True
    assert acceptance_is_kk(report)
    rag = next(line for line in report.lines if line.name == "rag_roundtrip_hit")
    assert rag.status == "SKIP"


def test_parse_does_not_pass_on_ok_true_or_partial():
    text = (
        "FAIL no_token_401 — HTTP 200 ok:False\n"
        "FAIL missing_field_422 — HTTP 200\n"
        "FAIL enum_422 — HTTP 200\n"
        "FAIL ui_served_200 — HTTP 404\n"
        "FAIL rag_roundtrip_hit — empty\n"
        "PASS single_persistence_root — one root\n"
        "FAIL ci_present_and_full_suite — missing\n"
        "PASS handler_bodies_distinct — 2\n"
        "PASS health_fail_closed — 503\n"
        "FAIL openapi_committed — missing\n"
        "FAIL cross_tenant_404 — HTTP 200\n"
        "FAIL docker_health_200 — STORE_DOCKER_HEALTH=''\n"
        "PASS authorship_floor — need≥5\n"
        f"ACCEPTANCE: 4/{len(ACCEPTANCE_CHECK_NAMES)}\n"
    )
    report = parse_acceptance_output(text)
    assert report.passed == 4
    assert report.ok is False
    assert not acceptance_is_kk(report)


def test_stamp_emits_measured_files(tmp_path):
    stamp_acceptance_into_path(tmp_path, product_name="RetailHub", cap_ids=["inventory"])
    assert (tmp_path / ACCEPTANCE_SCRIPT_REL).is_file()
    assert (tmp_path / "app" / "auth.py").is_file()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").is_file()
    assert "python -m pytest tests" in (tmp_path / ".github" / "workflows" / "ci.yml").read_text()
    assert '-m "not pilot"' not in (tmp_path / ".github" / "workflows" / "ci.yml").read_text()
    assert "authentication_required" in render_auth_module()
    openapi = (tmp_path / "docs" / "openapi.json").read_text()
    assert "/health" in openapi
    assert "/v1/inventory" in openapi
    assert (tmp_path / "app" / "static" / "index.html").is_file()


def test_export_blocker_refuses_authorship_only_green(tmp_path):
    status = {
        "cycle": "pilot",
        "pilot_ready": True,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"action_py": 6, "agent_written": 6},
    }
    blocked = acceptance_export_blocker(status, tmp_path)
    assert blocked is not None
    assert "STORE_ACCEPTANCE" in blocked
    assert f"0/{len(ACCEPTANCE_CHECK_NAMES)}" in blocked

    write_acceptance_report(
        tmp_path,
        AcceptanceReport(
            passed=len(ACCEPTANCE_CHECK_NAMES),
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=True,
            lines=[AcceptanceLine(name=n, status="PASS") for n in ACCEPTANCE_CHECK_NAMES],
        ),
    )
    assert acceptance_export_blocker(status, tmp_path) is None


def test_store_gate_runs_acceptance_inside_docker_argv(tmp_path):
    stamp_acceptance_into_path(tmp_path, product_name="Pilot", cap_ids=["orders"])
    calls = []

    def runner(argv, *, cwd=None, timeout=None):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "build" in argv:
            return _Proc(0, "built")
        if argv[:2] == ["docker", "run"]:
            return _Proc(0, "cid")
        if "acceptance.py" in joined:
            return _Proc(0, _kk_output())
        if "-c" in argv:
            return _Proc(0, "200\n")
        return _Proc(0, "")

    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        runner=runner,
        cycle="pilot",
    )
    res = gate_store_acceptance(ctx)
    assert res.ok, res.detail
    assert any(argv[:2] == ["docker", "build"] for argv in calls)
    assert any("scripts/acceptance.py" in " ".join(argv) for argv in calls)
    assert f"{len(ACCEPTANCE_CHECK_NAMES)}/{len(ACCEPTANCE_CHECK_NAMES)}" in res.detail
    assert "acceptance" in GATE_SCOPES["STORE"].lower() or "scripts/acceptance.py" in GATE_SCOPES["STORE"]


def test_store_gate_fails_when_image_is_not_kk(tmp_path):
    stamp_acceptance_into_path(tmp_path, product_name="Pilot", cap_ids=["orders"])

    def runner(argv, *, cwd=None, timeout=None):
        if "acceptance.py" in " ".join(argv):
            return _Proc(
                1,
                "FAIL no_token_401 — HTTP 200 ok:False\n"
                f"ACCEPTANCE: 0/{len(ACCEPTANCE_CHECK_NAMES)}\n",
            )
        if "-c" in argv:
            return _Proc(0, "200\n")
        return _Proc(0, "ok")

    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        runner=runner,
        cycle="pilot",
    )
    res = gate_store_acceptance(ctx)
    assert not res.ok
    assert f"0/{len(ACCEPTANCE_CHECK_NAMES)}" in res.detail


def test_store_gate_fails_closed_without_stamp(tmp_path):
    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        runner=lambda *a, **k: _Proc(0, ""),
        cycle="pilot",
    )
    res = gate_store_acceptance(ctx)
    assert not res.ok
    assert "missing" in res.detail.lower() or "not stamped" in res.detail.lower()


def test_read_report_prefers_workspace_file_over_stale_missing_status(tmp_path):
    stale = {
        "acceptance": {"missing": True, "passed": 0, "total": len(ACCEPTANCE_CHECK_NAMES), "ok": False},
        "level_grade": {
            "acceptance": {"missing": True, "passed": 0, "total": len(ACCEPTANCE_CHECK_NAMES), "ok": False},
        },
    }
    assert not workspace_acceptance_is_kk(tmp_path, stale)
    write_acceptance_report(
        tmp_path,
        AcceptanceReport(
            passed=len(ACCEPTANCE_CHECK_NAMES),
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=True,
            lines=[AcceptanceLine(name=n, status="PASS") for n in ACCEPTANCE_CHECK_NAMES],
        ),
    )
    report = read_acceptance_report(tmp_path, stale)
    assert report.missing is False
    assert report.passed == len(ACCEPTANCE_CHECK_NAMES)
    assert workspace_acceptance_is_kk(tmp_path, stale)
    status = {
        "cycle": "pilot",
        "pilot_ready": True,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        **stale,
    }
    assert acceptance_export_blocker(status, tmp_path) is None


def test_read_report_prefers_workspace_file_over_stale_measured_zero(tmp_path):
    """Live after a first Store miss: a measured 0/k (not missing) must not hide k/k."""
    stale_lines = [
        {"name": n, "status": "FAIL", "detail": "not measured"} for n in ACCEPTANCE_CHECK_NAMES
    ]
    stale = {
        "acceptance": {
            "missing": False,
            "passed": 0,
            "total": len(ACCEPTANCE_CHECK_NAMES),
            "ok": False,
            "lines": stale_lines,
        },
        "level_grade": {
            "acceptance": {
                "missing": False,
                "passed": 0,
                "total": len(ACCEPTANCE_CHECK_NAMES),
                "ok": False,
                "lines": stale_lines,
            },
        },
    }
    assert not workspace_acceptance_is_kk(tmp_path, stale)
    write_acceptance_report(
        tmp_path,
        AcceptanceReport(
            passed=len(ACCEPTANCE_CHECK_NAMES),
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=True,
            lines=[AcceptanceLine(name=n, status="PASS") for n in ACCEPTANCE_CHECK_NAMES],
        ),
    )
    report = read_acceptance_report(tmp_path, stale)
    assert report.missing is False
    assert report.passed == len(ACCEPTANCE_CHECK_NAMES)
    assert workspace_acceptance_is_kk(tmp_path, stale)
    assert acceptance_export_blocker(
        {
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            **stale,
        },
        tmp_path,
    ) is None


def test_store_gate_replaces_live_0_of_13_missing_with_measured_kk(tmp_path):
    """Live sess_4591d5cc shape: missing 0/13, then Store eval persists 13/13."""
    from app.factory.build_jobs import build_status
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.level_grade import attach_level_grade

    stamp_acceptance_into_path(tmp_path, product_name="Lettings", cap_ids=["unit_registry"])
    (tmp_path / "app" / "routes.py").write_text(
        "from app.auth import require_platform_token, reject_invalid_payload\n",
        encoding="utf-8",
    )
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc123def456")
    for role in (
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
        BuildRole.STORE_MANAGER,
    ):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        payload={"cycle": "pilot", "pilot_ready": True, "outcome": "SUCCESS"},
    )
    before = build_status(tmp_path)
    assert before["acceptance"]["passed"] == 0
    assert before["acceptance"]["total"] == len(ACCEPTANCE_CHECK_NAMES)
    assert acceptance_export_blocker(before, tmp_path) is not None

    def runner(argv, *, cwd=None, timeout=None):
        joined = " ".join(argv)
        if "acceptance.py" in joined:
            return _Proc(0, _kk_output())
        if "-c" in argv:
            return _Proc(0, "200\n")
        return _Proc(0, "ok")

    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        runner=runner,
        cycle="pilot",
    )
    res = gate_store_acceptance(ctx)
    assert res.ok, res.detail
    after = attach_level_grade(dict(before), tmp_path)
    assert after["acceptance"]["passed"] == len(ACCEPTANCE_CHECK_NAMES)
    assert after["acceptance"]["ok"] is True
    assert after["acceptance"]["missing"] is not True
    assert acceptance_export_blocker(after, tmp_path) is None
    reread = build_status(tmp_path)
    assert reread["acceptance"]["passed"] == len(ACCEPTANCE_CHECK_NAMES)
    assert acceptance_export_blocker(reread, tmp_path) is None


def test_store_gate_persists_kk_over_stale_measured_zero_after_pilot(tmp_path):
    """Pilot cycle wrote 13/13; a leftover measured 0/13 status must not stick."""
    from app.factory.build_jobs import build_status
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.level_grade import attach_level_grade

    stamp_acceptance_into_path(tmp_path, product_name="Lettings", cap_ids=["unit_registry"])
    (tmp_path / "app" / "routes.py").write_text(
        "from app.auth import require_platform_token, reject_invalid_payload\n",
        encoding="utf-8",
    )
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc123def456")
    for role in (
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
        BuildRole.STORE_MANAGER,
    ):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        payload={"cycle": "pilot", "pilot_ready": True, "outcome": "SUCCESS"},
    )
    stale_zero = {
        "missing": False,
        "passed": 0,
        "total": len(ACCEPTANCE_CHECK_NAMES),
        "ok": False,
        "lines": [
            {"name": n, "status": "FAIL", "detail": "not measured"}
            for n in ACCEPTANCE_CHECK_NAMES
        ],
    }
    before = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": True,
        "outcome": "SUCCESS",
        "acceptance": stale_zero,
        "level_grade": {
            "level": "CODE_GREEN",
            "acceptance": stale_zero,
            "three_gate": {"CODE": "PASS", "PRODUCT": "PASS", "STORE": "PASS"},
        },
    }
    assert read_acceptance_report(tmp_path, before).passed == 0

    def runner(argv, *, cwd=None, timeout=None):
        joined = " ".join(argv)
        if "acceptance.py" in joined:
            return _Proc(0, _kk_output())
        if "-c" in argv:
            return _Proc(0, "200\n")
        return _Proc(0, "ok")

    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        runner=runner,
        cycle="pilot",
    )
    res = gate_store_acceptance(ctx)
    assert res.ok, res.detail
    after = attach_level_grade(dict(before), tmp_path)
    assert after["acceptance"]["passed"] == len(ACCEPTANCE_CHECK_NAMES)
    assert after["acceptance"]["ok"] is True
    assert after["acceptance"]["missing"] is not True
    assert acceptance_export_blocker(after, tmp_path) is None
    reread = build_status(tmp_path)
    assert reread["acceptance"]["passed"] == len(ACCEPTANCE_CHECK_NAMES)
    assert acceptance_export_blocker(reread, tmp_path) is None


def test_store_gate_persists_score_onto_build_status(tmp_path):
    from app.factory.build_jobs import build_status
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.authority import BuildRole

    stamp_acceptance_into_path(tmp_path, product_name="Pilot", cap_ids=["orders"])
    (tmp_path / "app" / "routes.py").write_text(
        "from app.auth import require_platform_token, reject_invalid_payload\n",
        encoding="utf-8",
    )

    def runner(argv, *, cwd=None, timeout=None):
        joined = " ".join(argv)
        if "acceptance.py" in joined:
            return _Proc(0, _kk_output())
        if "-c" in argv:
            return _Proc(0, "200\n")
        return _Proc(0, "ok")

    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        runner=runner,
        cycle="pilot",
    )
    res = gate_store_acceptance(ctx)
    assert res.ok, res.detail
    assert (tmp_path / "docs" / "store_acceptance.json").is_file()

    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="pilot", inputs_hash="abc123def456")
    for role in BuildRole:
        if role in {BuildRole.COLLECTOR, BuildRole.CLONER, BuildRole.WRITER, BuildRole.TESTER, BuildRole.STORE_MANAGER}:
            ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
            ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        payload={"cycle": "pilot", "pilot_ready": True, "outcome": "SUCCESS"},
    )
    status = build_status(tmp_path)
    assert status["acceptance"]["passed"] == len(ACCEPTANCE_CHECK_NAMES)
    assert status["acceptance"]["total"] == len(ACCEPTANCE_CHECK_NAMES)
    assert status["acceptance"]["ok"] is True
    assert acceptance_export_blocker(status, tmp_path) is None


def test_runner_reopens_writer_when_pre_acceptance_pilot_needs_stamp(tmp_path):
    from app.factory.blueprint import load_blueprint
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner

    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    out = tmp_path / "ws"
    out.mkdir()
    runner = RoleRunner(bp, out, cycle="pilot", auto_pilot=False)
    ledger = runner.ledger
    ledger.start_run(product_id="smoke", inputs_hash="abc123def456aa")
    for role in (
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
        BuildRole.STORE_MANAGER,
    ):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        payload={"cycle": "pilot", "pilot_ready": True, "outcome": "SUCCESS"},
    )
    assert runner._acceptance_regrade_needed() is True
    assert runner._acceptance_writer_needed() is True
    runner._open_pilot_for_acceptance(reason="acceptance not k/k")
    done = runner.ledger.completed_roles()
    assert BuildRole.WRITER not in done
    assert BuildRole.TESTER not in done
    assert BuildRole.STORE_MANAGER not in done
    assert runner.ledger.pilot_ready() is False
    assert runner.cycle == "pilot"


def test_surface_incomplete_until_writer_stamp(tmp_path):
    assert acceptance_surface_incomplete(tmp_path) is True
    stamp_acceptance_into_path(tmp_path, product_name="Pilot", cap_ids=["orders"])
    assert acceptance_surface_incomplete(tmp_path) is True
    (tmp_path / "app" / "routes.py").write_text(
        "from app.auth import require_platform_token, reject_invalid_payload\n",
        encoding="utf-8",
    )
    assert acceptance_surface_incomplete(tmp_path) is False


def test_ci_render_is_full_suite_not_code_phase_only():
    text = render_github_ci()
    assert "python -m pytest tests" in text
    assert "not pilot" not in text


def test_openapi_render_is_openapi3_with_health(tmp_path):
    text = render_openapi("X", ["estate_registry"])
    assert '"openapi": "3.0.3"' in text
    assert "/health" in text
    assert "/v1/estate_registry" in text


class _EchoResp:
    """Simulates an endpoint that echoes its own request instead of doing
    real retrieval -- exactly the live failure mode found in a generated
    product (app/rag_routes.py's response always carries "query": query).
    """

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class _EchoOnlyHttp:
    """No corpus, no retrieval -- every /rag/ingest 200s, every /rag/query
    just echoes the request fields back verbatim, the way a naive or
    disconnected RAG stub would. A correct check must FAIL against this.
    """

    def request(self, method, path, json=None, headers=None, **_kw):
        body = json or {}
        if path in ("/v1/rag/ingest", "/v1/steward/rag/ingest"):
            return _EchoResp(200, {"ok": True})
        if path in ("/v1/rag/query", "/v1/steward/rag/query"):
            return _EchoResp(200, {"ok": True, "query": body.get("query"), "hits": []})
        return _EchoResp(404, {})


class _RealRetrievalHttp:
    """A genuine plant->retrieve implementation: only the planted nonce
    comes back as a hit; an unplanted nonce never does. A correct check
    must PASS against this.
    """

    def __init__(self):
        self.corpus = []

    def request(self, method, path, json=None, headers=None, **_kw):
        body = json or {}
        if path == "/v1/rag/ingest":
            self.corpus.append(body.get("text", ""))
            return _EchoResp(200, {"ok": True})
        if path == "/v1/rag/query":
            q = str(body.get("query") or "")
            hits = [doc for doc in self.corpus if q and q in doc]
            return _EchoResp(200, {"ok": True, "hits": hits})
        return _EchoResp(404, {})


def _load_check_rag_roundtrip_hit(tmp_path):
    script = render_acceptance_script()
    ns: dict = {"__file__": str(tmp_path / "scripts" / "acceptance.py"), "__name__": "acceptance_under_test"}
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    exec(compile(script, "acceptance.py", "exec"), ns)
    return ns["check_rag_roundtrip_hit"], ns


def test_rag_roundtrip_hit_fails_closed_against_an_echo_only_stub(tmp_path):
    """The check the owner and an audit fork both found vacuous: it must not
    be satisfiable by an endpoint that merely echoes its own request. This
    executes the check AS RENDERED (not a hand-copied duplicate), so it can
    never silently drift from what every generated product actually ships.
    """
    check_fn, ns = _load_check_rag_roundtrip_hit(tmp_path)
    ns["_has_rag_surface"] = lambda: True
    status, detail = check_fn(_EchoOnlyHttp())
    assert status == "FAIL", f"echo-only stub must not pass rag_roundtrip_hit, got: {status} {detail}"


def test_rag_roundtrip_hit_passes_against_real_retrieval(tmp_path):
    check_fn, ns = _load_check_rag_roundtrip_hit(tmp_path)
    ns["_has_rag_surface"] = lambda: True
    status, detail = check_fn(_RealRetrievalHttp())
    assert status == "PASS", f"genuine plant->retrieve must pass, got: {status} {detail}"
