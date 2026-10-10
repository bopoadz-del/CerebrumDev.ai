"""Through the real run loop: the FIRST owned-file write rejects the pass, the
Factory's files are restored, and the writer is re-prompted with the owned set
AND the findings it still owes -- no rework round consumed (owner spec, cycle 9).

Live co-op anchor (sess_e41f2375a98342d1): a rework pass touched
app/domain_ops.py; the re-prompt named only that file, the next pass never
heard the rework finding, and the build stopped SAME_FAILURE_TWICE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
FINDING = "FAILED tests/test_routes.py::test_r - quillfeather refused the round trip"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_a_reprompt_hands_the_next_pass_the_owned_set_and_the_open_finding(
    tmp_path, stub_coder, monkeypatch
):
    from app.factory.blueprint import load_blueprint
    from app.factory.build import brief_gates, factory_owned
    from app.factory.build import runner as runner_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateResult
    from app.factory.build.ledger import EventKind
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.runner import RoleRunner

    real = runner_mod.gate_for
    calls = {BuildRole.WRITER: 0, BuildRole.TESTER: 0}
    touched = [{"path": "conftest.py", "change": "modified"}]

    def gate_for(role):
        gate = real(role)
        role = BuildRole(role)
        if role not in calls:
            return gate

        def scripted(ctx):
            calls[role] += 1
            if role is BuildRole.TESTER and calls[role] == 1:
                return GateResult(ok=False, gate="suite_green", reason="suite_red",
                                  detail="suite is red", findings=[FINDING],
                                  payload={"check": brief_gates.SUITE_CHECK})
            if role is BuildRole.WRITER and calls[role] == 2:
                return GateResult(
                    ok=False, gate=brief_gates.WRITER_CONTRACT_CHECK,
                    reason=factory_owned.WRITER_AUTHORED,
                    detail=f"{factory_owned.WRITER_AUTHORED}: the writer touched conftest.py",
                    findings=factory_owned.rework_findings(touched),
                    payload={"touched": touched},
                )
            return gate(ctx)

        return scripted

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    seen: list = []
    real_writer = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        seen.append([str(x) for x in ctx.work_list])
        return real_writer(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    runner = RoleRunner(load_blueprint(SMOKE), tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert len(seen) >= 3, seen
    rework_pass, reprompt_pass = seen[1], seen[2]
    assert any("quillfeather" in item for item in rework_pass), rework_pass
    # The re-prompted pass still owes the rework finding...
    assert any("quillfeather" in item for item in reprompt_pass), reprompt_pass
    # ...is told the touched file and the owned set...
    assert any("conftest.py: modified" in item for item in reprompt_pass), reprompt_pass
    owned_line = [item for item in reprompt_pass if item.startswith("[factory_owned] owned set")]
    assert owned_line and "conftest.py" in owned_line[0], reprompt_pass
    # ...and no rework round was spent on the re-prompt.
    assert len([e for e in runner.ledger.events() if e.kind is EventKind.REWORK]) == 1
