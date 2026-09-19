"""The writer prompt names entities the way TESTER calls them.

Prompt v3/v4 told the agent to write ``save(entity, ...)`` and
``op.create_table("<entity>")`` but never said what an entity is called.
TESTER defaults every entity to the capability id and the emitted
data-lifecycle suite calls ``store.save("<capability>", ...)``, so agents
that named tables their own way went red in round 1 on a KeyError --
``'report_namemetric_name'`` on the vet build, and
``'branch_and_consolidated_operations'`` on bakery, which ran on v4 --
costing a rework round per build.
"""

from __future__ import annotations

import inspect

from app.factory.build.data_lifecycle import render_product_tests
from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt


class _Blueprint:
    product_id = "bakery-operations"
    product_name = "Bakery Branch Operations Platform"
    vertical = "bakery"
    summary = "branch ops"


def _prompt() -> str:
    return render_writer_prompt(_Blueprint(), brief="a bakery")


def test_the_prompt_says_an_entity_is_named_by_its_capability_id():
    text = _prompt()
    assert "An entity's name IS its capability id" in text
    assert 'op.create_table("<capability>")' in text
    for call in ("store.save", "store.get", "store.list_all"):
        assert f'{call}("<capability>"' in text, call


def test_the_prompt_rule_and_testers_default_move_together():
    """The divergence guard.

    If TESTER ever stops defaulting an entity to the capability id, the
    prompt's rule becomes a lie and this fails -- change both, together.
    """
    from app.factory.build import roles_handlers

    tester = inspect.getsource(roles_handlers.run_tester)
    assert 'spec["entity"] = cid.replace("-", "_")' in tester, (
        "TESTER no longer names entities by capability id; update the "
        "writer prompt's PERSISTENCE rule to match"
    )
    assert "An entity's name IS its capability id" in _prompt()


def test_the_emitted_suite_calls_the_store_with_the_capability_id():
    suite = render_product_tests(
        {
            "branch_and_consolidated_operations": {
                "entity": "branch_and_consolidated_operations",
                "fields": [{"name": "branch", "type": "string"}],
            }
        }
    )
    assert "ENTITY = 'branch_and_consolidated_operations'" in suite
    assert "store.save(ENTITY" in suite


def test_the_version_was_bumped_for_the_contract_change():
    # The entity rule arrived in v5; later versions must keep it.
    assert int(PROMPT_VERSION.rsplit(".v", 1)[1]) >= 5
    assert _prompt().startswith(f"<!-- {PROMPT_VERSION} -->")


def test_the_prompt_is_still_deterministic():
    assert _prompt() == _prompt()
