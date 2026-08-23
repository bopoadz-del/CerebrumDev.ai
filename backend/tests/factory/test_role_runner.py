"""The role runner must fail loudly and resume honestly.

New-shape tests for the manufacturing runner. The hazards here are all
orchestration-layer versions of the ones the gates were written against: a run
that reports success without its gates green, a role that escapes its lane and
is quietly tolerated, a resume that silently rebuilds from zero, and a
subprocess payload that only ever gets mocked.

The end-to-end test spawns real child processes (compileall, the import probe,
pytest inside the generated platform). It needs no LLM key -- the WRITER's
deterministic path is the one CI exercises.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind, LedgerError
from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult
from app.factory.build.runner import (
    BuildBudget,
    Outcome,
    RoleRunner,
    blueprint_hash,
    runner_enabled,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    """These tests are about the RUNNER, not the coder.

    FACTORY_CODER_ENABLED defaults to 1, so on any machine with a live key in
    backend/.env every build here hit the real LLM: 399s locally against 29s
    in CI, real money per run, and non-determinism injected into tests that
    have nothing to do with the coder. Pinned off; the coder path is covered
    by tests/factory/test_writer_coder_wiring.py, which mocks it and asserts
    authorship, fallback, disabled-state and rework hand-off.

    Individual tests below re-enable it deliberately with a stub.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def _phase_starts(ledger: BuildLedger, role: BuildRole) -> int:
    return sum(
        1
        for e in ledger.events()
        if e.kind is EventKind.PHASE_STARTED and e.role is role
    )


def _tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or "__pycache__" in rel or ".pytest_cache" in rel:
            continue
        if rel.endswith("build_ledger.jsonl"):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# -- the real end-to-end build -------------------------------------------


def test_end_to_end_build_reaches_success_with_every_gate_passed(blueprint, tmp_path):
    """One real build, real gates, real subprocesses, no LLM key."""
    runner = RoleRunner(blueprint, tmp_path / "build", budget=BuildBudget(max_rework=2))
    outcome = runner.run()

    assert outcome.ok, outcome.to_dict()
    assert outcome.outcome is Outcome.SUCCESS
    assert outcome.rework_used == 0
    assert [p.value for p in outcome.completed] == [
        "COLLECTOR",
        "CLONER",
        "WRITER",
        "TESTER",
        "STORE_MANAGER",
    ]

    ledger = runner.ledger
    assert ledger.succeeded()
    assert ledger.terminal_event().kind is EventKind.RUN_SUCCEEDED
    # Every phase reached a passing gate -- not merely "no failures recorded".
    passed = {e.role for e in ledger.events() if e.kind is EventKind.GATE_PASSED}
    assert passed == set(BuildRole)


def test_generated_platform_makes_no_store_callback(blueprint, tmp_path):
    """The whole point of the rebuild: the artifact runs without the store.

    The old template path emitted httpx.post(store_url + "/v1/execute") into
    every REUSE handler, making each delivered platform a client of the
    operator's uptime.
    """
    out = tmp_path / "build"
    RoleRunner(blueprint, out).run()

    offenders = []
    for path in out.rglob("*.py"):
        if "tests" in path.relative_to(out).parts:
            continue  # the smoke test names the vars in order to strip them
        text = path.read_text(encoding="utf-8")
        for needle in ("httpx", "/v1/execute", "CEREBRUM_API_URL", "requests.post"):
            if needle in text:
                offenders.append(f"{path.relative_to(out)}: {needle}")
    assert not offenders, offenders

    assert (out / "app" / "dispatch.py").is_file()
    assert (out / "vendor" / "blocks" / "analytics" / "block.py").is_file()
    lock = json.loads((out / "blocks.lock.json").read_text(encoding="utf-8"))
    assert set(lock["blocks"]) == {"analytics", "dashboard"}


def test_the_generated_platform_suite_really_runs_in_a_subprocess(blueprint, tmp_path):
    """Rule: a payload that runs in a child process gets a spawning test.

    The runner's gates shell out to compileall, the import probe and pytest.
    Mocking those would leave the command strings unexercised -- exactly the
    class of bug that already shipped once here.
    """
    out = tmp_path / "build"
    runner = RoleRunner(blueprint, out)
    assert runner.subprocess_runner is None, "this test must use the real runner"
    outcome = runner.run()
    assert outcome.ok

    tester = [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.GATE_PASSED and e.role is BuildRole.TESTER
    ][-1]
    assert "passed" in tester.detail, tester.detail

    # And independently: the artifact's suite passes from a clean environment.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--no-header"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_artifact_is_a_platform_not_a_parts_list(blueprint, tmp_path):
    """Every artifact class a delivered platform needs is present.

    The runner used to emit ~8 files -- handlers, vendored blocks, a lockfile
    and an import-only smoke test. That is a parts list: it cannot be run,
    stores nothing, and exposes nothing.
    """
    out = tmp_path / "build"
    assert RoleRunner(blueprint, out).run().ok

    for rel in (
        "app/models.py",
        "app/store.py",
        "app/migrations.py",
        "app/backup.py",
        "app/health.py",
        "app/observe.py",
        "app/revision.py",
        "app/routes.py",
        "app/jobs.py",
        "app/main.py",
        "app/dispatch.py",
        "alembic.ini",
        "alembic/env.py",
        "alembic/versions/0001_baseline.py",
        "alembic/versions/0002_lifecycle_audit.py",
        "scripts/entrypoint.sh",
        "scripts/rollback.sh",
        "docs/data_lifecycle.json",
        "docs/deploy.json",
        "docs/domain_acceptance.json",
        "app/domain_ops.py",
        "app/work_queue.py",
        "README.md",
        "requirements.txt",
        "tests/conftest.py",
        "tests/test_models.py",
        "tests/test_data_lifecycle.py",
        "tests/test_deploy.py",
        "tests/test_domain_acceptance.py",
        "tests/test_routes.py",
        "tests/test_smoke.py",
        "blocks.lock.json",
    ):
        assert (out / rel).is_file(), f"missing {rel}"

    # Persistence and models are generated from one spec, so their columns
    # cannot drift apart. Schema is Alembic, not connect()-time DDL.
    store_src = (out / "app" / "store.py").read_text(encoding="utf-8")
    models_src = (out / "app" / "models.py").read_text(encoding="utf-8")
    assert "CREATE TABLE" not in store_src
    assert "PRAGMA journal_mode=WAL" in store_src
    mig = (out / "alembic" / "versions" / "0001_baseline.py").read_text(
        encoding="utf-8"
    )
    assert "analytics_surface" in mig
    assert "def upgrade" in mig and "def downgrade" in mig
    assert "class AnalyticsSurface" in models_src
    assert "MODELS = {" in models_src

    routes_src = (out / "app" / "routes.py").read_text(encoding="utf-8")
    assert '@router.post("/analytics_surface")' in routes_src
    assert '@router.get("/analytics_surface")' in routes_src
    assert '@router.get("/analytics_surface/{item_id}")' in routes_src
    assert '@router.put("/analytics_surface/{item_id}")' in routes_src
    assert '@router.delete("/analytics_surface/{item_id}")' in routes_src
    assert '@router.post("/work_queue")' in routes_src
    for path in (
        "/jobs",
        "/catalog",
        "/inventory",
        "/capabilities",
        "/gates",
        "/provenance",
    ):
        assert f'@router.get("{path}")' in routes_src
    jobs_src = (out / "app" / "jobs.py").read_text(encoding="utf-8")
    assert "Binding surveyor" in jobs_src
    assert "runs_over_http" in jobs_src
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "GET /v1/jobs" in readme
    assert "Binding surveyor" in readme


def test_the_platform_suite_exercises_the_surface_it_does_not_just_import(
    blueprint, tmp_path
):
    """An import-only suite passes on a platform whose storage is broken."""
    out = tmp_path / "build"
    assert RoleRunner(blueprint, out).run().ok

    models_test = (out / "tests" / "test_models.py").read_text(encoding="utf-8")
    assert "store.save(" in models_test and "store.get(" in models_test
    assert "did not persist" in models_test
    lifecycle = (out / "tests" / "test_data_lifecycle.py").read_text(encoding="utf-8")
    assert "test_restore_drill_backup_wipe_restore_rows" in lifecycle
    assert "test_schema_change_applies_to_populated_v1_and_rolls_back" in lifecycle
    assert "test_parallel_writes_match_fastapi_threadpool" in lifecycle
    domain = (out / "tests" / "test_domain_acceptance.py").read_text(encoding="utf-8")
    assert "perform_all" in domain
    assert "@pytest.mark.pilot" in domain
    assert "execute_action" in (out / "app" / "domain_ops.py").read_text(encoding="utf-8")
    store_src = (out / "app" / "store.py").read_text(encoding="utf-8")
    assert "def update(" in store_src
    assert "def delete(" in store_src

    routes_test = (out / "tests" / "test_routes.py").read_text(encoding="utf-8")
    assert "TestClient" in routes_test
    assert "client.post(" in routes_test
    assert "/v1/jobs" in routes_test
    assert "/v1/gates" in routes_test
    assert "runs_over_http" in routes_test
    # Code-phase: the route answers JSON. Store acceptance is @pytest.mark.pilot.
    assert "def test_every_capability_route_answers():" in routes_test
    assert "@pytest.mark.pilot" in routes_test
    assert "def test_every_capability_route_accepts_payload():" in routes_test
    assert '.get("ok") is False' in routes_test
    assert "rejected a payload built from its own schema" in routes_test
    marker_at = routes_test.index("@pytest.mark.pilot")
    reject_at = routes_test.index("rejected a payload built from its own schema")
    assert marker_at < reject_at

    smoke = (out / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    assert ".handle(" in smoke, "capabilities must be executed, not imported"
    assert "def test_every_capability_handle_returns_mapping():" in smoke
    assert "@pytest.mark.pilot" in smoke

    jobs_src = (out / "app" / "jobs.py").read_text(encoding="utf-8")
    assert '"gated": False' in jobs_src or "'gated': False" in jobs_src
    assert "pilot" in jobs_src


def test_the_writer_still_cannot_write_the_tests_after_the_lane_widened(
    blueprint, tmp_path
):
    """The scaffold lanes must not have opened a path into tests/.

    README.md, requirements.txt and pyproject.toml were added to the WRITER's
    lane so it can emit a runnable project. That widening must not have been
    a root wildcard.
    """
    from app.factory.build.authority import AuthorityError, assert_write_allowed

    ws = tmp_path / "w"
    ws.mkdir()
    for allowed in ("README.md", "requirements.txt", "pyproject.toml"):
        assert assert_write_allowed(BuildRole.WRITER, ws / allowed, workspace=ws)
    for denied in ("tests/test_x.py", "vendor/blocks/x/block.py", "blocks.lock.json", "setup.py"):
        with pytest.raises(AuthorityError):
            assert_write_allowed(BuildRole.WRITER, ws / denied, workspace=ws)


# -- failure is a failure ------------------------------------------------


def test_rework_budget_exhaustion_fails_the_run(blueprint, tmp_path):
    """A tester that never goes green must end FAILED, never SUCCESS."""

    def barren_tester(ctx):
        # Writes no tests at all; gate_suite_green fails this for real.
        return RoleResult(ok=True, detail="wrote nothing")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = barren_tester

    runner = RoleRunner(
        blueprint, tmp_path / "build", roles=roles, budget=BuildBudget(max_rework=2)
    )
    outcome = runner.run()

    assert not outcome.ok
    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT
    assert outcome.rework_used == 2
    assert outcome.failed_phase is BuildRole.TESTER
    assert runner.ledger.terminal_event().kind is EventKind.RUN_FAILED
    assert runner.ledger.succeeded() is False
    # The writer really was sent back round, twice.
    assert _phase_starts(runner.ledger, BuildRole.WRITER) == 3
    assert runner.ledger.rework_counts() == {"WRITER": 2}


def test_wall_clock_budget_exhaustion_fails_the_run(blueprint, tmp_path):
    """Time runs out mid-build: FAILED_BUDGET_SPENT, not a partial success."""
    ticks = iter([0.0, 0.0, 5.0, 500.0, 500.0, 500.0, 500.0, 500.0])

    runner = RoleRunner(
        blueprint,
        tmp_path / "build",
        budget=BuildBudget(wall_clock_s=100.0),
        clock=lambda: next(ticks),
    )
    outcome = runner.run()

    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT
    assert not outcome.ok
    assert "wall-clock budget" in outcome.detail
    assert runner.ledger.terminal_event().kind is EventKind.RUN_FAILED


def test_a_failed_non_tester_gate_is_terminal(blueprint, tmp_path):
    """Only the TESTER has a role positioned to act on its findings."""

    def empty_cloner(ctx):
        # Claims blocks were vendored but writes nothing -- the cloner gate
        # catches the mismatch between the ledger and the disk.
        return RoleResult(ok=True, detail="lied", vendored_blocks=("analytics",))

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.CLONER] = empty_cloner

    runner = RoleRunner(blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert outcome.outcome is Outcome.FAILED_GATE
    assert outcome.failed_phase is BuildRole.CLONER
    assert outcome.findings, "the gate's findings must reach the outcome"
    assert runner.ledger.rework_counts() == {}


def test_a_role_that_raises_ends_the_run(blueprint, tmp_path):
    def broken_collector(ctx):
        from app.factory.build.roles import RoleError

        raise RoleError("registry unreachable")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.COLLECTOR] = broken_collector

    runner = RoleRunner(blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR
    assert "registry unreachable" in outcome.detail
    aborted = [e for e in runner.ledger.events() if e.kind is EventKind.PHASE_ABORTED]
    assert aborted and aborted[-1].role is BuildRole.COLLECTOR


# -- lanes ---------------------------------------------------------------


def test_a_role_writing_outside_its_lane_fails_the_build(blueprint, tmp_path):
    """The authority kernel must abort the run, not be caught and ignored."""

    def overreaching_tester(ctx):
        # TESTER owns tests/ only. Patching the code under test is the exact
        # thing the lane exists to prevent.
        ctx.workspace.write_text("app/actions/analytics_surface.py", "# nope\n")
        return RoleResult(ok=True, detail="should never be reached")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = overreaching_tester

    runner = RoleRunner(blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert outcome.outcome is Outcome.FAILED_AUTHORITY
    assert outcome.failed_phase is BuildRole.TESTER
    assert "outside its lane" in outcome.detail
    assert runner.ledger.terminal_event().kind is EventKind.RUN_FAILED
    # And the file it tried to clobber is still the writer's version.
    handler = (tmp_path / "build" / "app" / "actions" / "analytics_surface.py").read_text(
        encoding="utf-8"
    )
    assert "# nope" not in handler


def test_the_writer_cannot_edit_the_tests_that_judge_it(blueprint, tmp_path):
    def self_grading_writer(ctx):
        ctx.workspace.write_text("tests/test_smoke.py", "def test_ok(): assert True\n")
        return RoleResult(ok=True, detail="never")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = self_grading_writer

    outcome = RoleRunner(blueprint, tmp_path / "build", roles=roles).run()
    assert outcome.outcome is Outcome.FAILED_AUTHORITY
    assert outcome.failed_phase is BuildRole.WRITER


# -- resume --------------------------------------------------------------


class _Boom(BaseException):
    """Stands in for a killed process: not caught by the runner."""


def test_resume_picks_up_where_the_kill_happened(blueprint, tmp_path):
    """Kill mid-build, restart, land on the same terminal state.

    Asserted by counting PHASE_STARTED per role: a resume that silently
    rebuilds from zero would run COLLECTOR and CLONER twice.
    """
    out = tmp_path / "build"

    def dying_writer(ctx):
        raise _Boom("process killed")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = dying_writer

    first = RoleRunner(blueprint, out, roles=roles)
    with pytest.raises(_Boom):
        first.run()

    ledger = BuildLedger(out / "build_ledger.jsonl")
    assert ledger.terminal_event() is None, "a killed run must record no verdict"
    assert ledger.completed_roles() == {BuildRole.COLLECTOR, BuildRole.CLONER}
    assert ledger.resume_point() is BuildRole.WRITER

    second = RoleRunner(blueprint, out, budget=BuildBudget(max_rework=2))
    outcome = second.run()

    assert outcome.ok, outcome.to_dict()
    final = BuildLedger(out / "build_ledger.jsonl")
    assert final.succeeded()
    # Earlier phases were not redone.
    assert _phase_starts(final, BuildRole.COLLECTOR) == 1
    assert _phase_starts(final, BuildRole.CLONER) == 1
    # And the resumed phase ran exactly once (plus its killed attempt).
    assert _phase_starts(final, BuildRole.WRITER) == 2


def test_resume_against_a_changed_blueprint_is_refused(blueprint, tmp_path):
    out = tmp_path / "build"
    RoleRunner(blueprint, out).run()

    other = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    with pytest.raises(LedgerError, match="cannot resume"):
        RoleRunner(other, out).run()


# -- determinism ---------------------------------------------------------


def test_blueprint_hash_is_the_resume_key_and_is_stable(blueprint):
    """Resume must key on inputs, never on the generated tree.

    ProductGenerator's "inputs_hash" is really hash_tree of the output, so it
    moves whenever an LLM writes a handler; keying resume on it would refuse
    every resume of an unchanged blueprint.
    """
    hashes = {blueprint_hash(load_blueprint(SMOKE)) for _ in range(3)}
    assert len(hashes) == 1
    assert blueprint_hash(load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")) not in hashes


def test_two_runs_produce_an_identical_artifact(blueprint, tmp_path, monkeypatch):
    """Byte-reproducible with the coding agent off.

    Scoped deliberately. Once the coder is wired in, whole-tree equality is
    only achievable while the agent is idle -- an LLM does not emit the same
    bytes twice. The coder-on guarantee is the narrower one asserted below.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    a, b = tmp_path / "a", tmp_path / "b"
    assert RoleRunner(blueprint, a).run().ok
    assert RoleRunner(blueprint, b).run().ok

    tree_a, tree_b = _tree(a), _tree(b)
    assert set(tree_a) == set(tree_b)
    drifted = {k for k in tree_a if tree_a[k] != tree_b[k]}
    assert not drifted, sorted(drifted)


def test_coder_nondeterminism_is_confined_to_the_handlers(
    blueprint, tmp_path, monkeypatch
):
    """With the agent writing, only app/actions/ may differ between builds.

    Stubbed rather than calling a live model, so this runs in CI with no key
    and still fails if agent output ever bleeds into the vendored blocks, the
    lockfile, the dispatch runtime or the tests.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    counter = {"n": 0}

    def varying(**kwargs):
        counter["n"] += 1
        return {
            "body": f'    return {{"capability": CAPABILITY_ID, "run": {counter["n"]}}}',
            "model": "stub-coder",
        }

    monkeypatch.setattr("app.factory.coder.generate_platform_handler", varying)
    # Every other coder entry point is stubbed *stably*, so the only source of
    # variation is the handler -- and no entry point reaches a live API.
    monkeypatch.setattr(
        "app.factory.coder.generate_model_spec",
        lambda **kw: {
            "entity": kw["capability_id"].replace("-", "_"),
            "fields": [{"name": "reference", "type": "str", "required": True}],
            "model": "stub-spec",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_route_body",
        lambda **kw: {
            "body": '    return {"ok": True, "capability": CAPABILITY_ID}',
            "model": "stub-route",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder._llm_code_call", lambda messages: "# stub readme\n"
    )

    a, b = tmp_path / "a", tmp_path / "b"
    assert RoleRunner(blueprint, a).run().ok
    assert RoleRunner(blueprint, b).run().ok
    tree_a, tree_b = _tree(a), _tree(b)

    assert set(tree_a) == set(tree_b)
    drifted = {k for k in tree_a if tree_a[k] != tree_b[k]}
    assert drifted, "the stub varies per call; something must differ"
    # package_identity.json is a digest of the tree; it must move when
    # handlers move. It is factory-stamped, not agent-authored.
    escaped = {
        k
        for k in drifted
        if not k.startswith("app/actions/") and k != "docs/package_identity.json"
    }
    assert not escaped, f"agent output escaped into {sorted(escaped)}"


# -- flag ----------------------------------------------------------------


def test_runner_is_opt_in(monkeypatch):
    """The template path stays the default until an explicit cutover."""
    monkeypatch.delenv("FACTORY_RUNNER_ENABLED", raising=False)
    assert runner_enabled() is False
    monkeypatch.setenv("FACTORY_RUNNER_ENABLED", "1")
    assert runner_enabled() is True
    monkeypatch.setenv("FACTORY_RUNNER_ENABLED", "0")
    assert runner_enabled() is False


def test_gates_are_not_injectable(blueprint, tmp_path):
    """A role must not be able to supply the check that judges it.

    Roles are injectable (tests above depend on it); gates deliberately are
    not. If a `gates=` parameter ever appears, this fails and the reviewer
    gets to ask why.
    """
    import inspect

    params = set(inspect.signature(RoleRunner.__init__).parameters)
    assert "roles" in params
    assert "gates" not in params, "gates must stay looked-up-by-phase"
