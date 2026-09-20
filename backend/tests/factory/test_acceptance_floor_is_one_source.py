"""One spec, two consumers — and a test that fails when they drift.

The Store gate graded every build against thirteen checks the coder was never
told. Measured, not assumed: before this landed, grepping the writer prompt
and the C-BRIEF for ``no_token_401`` returned zero, and the same for every
other check id. So the agent discovered the floor by failing it, and each
discovery cost a full writer pass.

Putting the rules in the brief alone would move the trust back to the author,
which is what the gate exists to remove. So the floor lives in one file and
both sides read it: the prompt renders ``requirement``, the gate takes its
checklist from the ids. This module is the part that keeps that true — if
either consumer grows its own copy, this goes red.
"""

from __future__ import annotations

from app.factory.build.acceptance_floor import (
    check_ids,
    checks,
    floor_version,
    render_for_prompt,
    requirements,
)
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
)
from app.factory.build.writer_prompt import render_writer_prompt


class _Blueprint:
    product_id = "bakery-operations"
    product_name = "Bakery Branch Operations Platform"
    vertical = "bakery"
    summary = "branch ops"


def _prompt() -> str:
    return render_writer_prompt(_Blueprint(), brief="a bakery")


class TestTheGateReadsTheFloor:
    def test_the_checklist_is_not_a_second_copy(self):
        assert ACCEPTANCE_CHECK_NAMES == check_ids()

    def test_the_required_count_matches_what_the_floor_actually_holds(self):
        assert ACCEPTANCE_REQUIRED == len(check_ids())

    def test_authorship_floor_stays_last(self):
        """The gate's own assertion, restated where the order is decided."""
        assert check_ids()[-1] == "authorship_floor"


class TestTheCoderIsToldTheFloor:
    def test_every_check_the_gate_grades_appears_in_the_prompt(self):
        """The regression this file exists for.

        Before the floor was one file, this assertion failed on all 13.
        """
        text = _prompt()
        missing = [cid for cid in check_ids() if cid not in text]
        assert not missing, f"graded on but never told: {missing}"

    def test_the_requirement_text_is_rendered_not_paraphrased(self):
        text = _prompt()
        missing = [r for r in requirements() if r not in text]
        assert not missing, f"requirement reworded between spec and prompt: {missing}"

    def test_the_prompt_names_the_floor_version(self):
        """A prompt and a gate on different versions is the drift itself."""
        assert f"v{floor_version()}" in render_for_prompt()
        assert f"v{floor_version()}" in _prompt()

    def test_the_floor_is_stated_as_a_bar_not_as_advice(self):
        assert "not advice" in _prompt()


class TestTheSpecIsWellFormed:
    def test_every_check_says_both_what_to_build_and_what_is_verified(self):
        for check in checks():
            assert check["requirement"].strip(), check["id"]
            assert check["check"].strip(), check["id"]

    def test_requirements_are_written_for_the_builder_not_the_grader(self):
        """A requirement that only restates the check teaches the coder
        nothing it could not have guessed from the failure message."""
        for check in checks():
            assert check["requirement"].strip() != check["check"].strip(), check["id"]

    def test_ids_are_unique(self):
        assert len(set(check_ids())) == len(check_ids())
