"""BRIEF LINT rejects a broken brief before the coder session opens."""

from __future__ import annotations

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import BriefLintError, lint_brief, lint_or_raise
from app.factory.product_architect import plan_blueprint
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"


def _compiled(path=SMOKE):
    bp = load_blueprint(path)
    return compile_brief(bp, plan_blueprint(bp))


def test_smoke_and_lettings_briefs_lint_clean():
    for path in (SMOKE, LETTINGS):
        compiled = _compiled(path)
        result = lint_brief(compiled)
        assert result.ok, result.errors


def test_mutation_unresolved_block_id_is_rejected():
    compiled = _compiled()
    compiled.missing_reuse = ["phantom_block_xyz"]
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("unresolved block id" in e for e in result.errors)
    with pytest.raises(BriefLintError, match="phantom_block_xyz"):
        lint_or_raise(compiled)


def test_mutation_missing_budget_is_rejected():
    compiled = _compiled()
    compiled.budget_s = 0
    compiled.text = compiled.text.replace("Budget wall:", "No wall named:")
    compiled.text = compiled.text.replace("1800s", "unset")
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("missing budget" in e for e in result.errors)


def test_mutation_acceptance_without_check_is_rejected():
    compiled = _compiled()
    compiled.text = compiled.text.replace(
        "ACCEPTANCE (harness, not the coder)",
        "ACCEPTANCE (harness, not the coder)\n\n- feel good about the screens",
    )
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("acceptance line without executable check" in e for e in result.errors)


def test_mutation_planted_unsourced_line_is_rejected():
    compiled = _compiled()
    compiled.text = compiled.text + "\nInvent a loyalty program the customer never asked for.\n"
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("orphan line" in e for e in result.errors)


def test_mutation_invented_scope_line_is_rejected():
    compiled = _compiled()
    compiled.text = (
        compiled.text
        + "\nInvent READS=loyalty_points the customer never declared on block.json.\n"
    )
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("orphan line" in e for e in result.errors)


def test_mutation_drops_writer_behaviour_acceptance():
    compiled = _compiled()
    compiled.text = compiled.text.replace(
        "every capability accepts a POST built from its own FIELDS/CONSTRAINTS",
        "every capability looks fine in the demo",
    )
    compiled.text = compiled.text.replace("[check:writer_behaviour]", "")
    compiled.text = compiled.text.replace("writer_behaviour", "writer_other")
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("writer_behaviour schema-accept" in e for e in result.errors)


def test_unfilled_template_slot_is_rejected():
    compiled = _compiled()
    compiled.text = compiled.text + "\n{{ORPHAN_SLOT}}\n"
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("unfilled template slot" in e for e in result.errors)
