"""What the Floor model is told about the reasoning kit's questions.

This is the only place the domain owner's question sheet reaches the model that
runs a build. If it tells the model the wrong thing, the model asks the wrong
questions or, worse, presents a derived question as the domain's own and gets an
answer no rule can then use.

The assertions are about what the prompt CANNOT say:

  * never that an un-interviewed domain is in hand
  * never a derived question without saying it is derived
  * never a paraphrase of the owner's wording
  * never that an answer is held because a question was asked
"""
from __future__ import annotations

import pathlib
import types

import pytest
import yaml

from app.factory import platform_chat_llm


def _state(vertical):
    return types.SimpleNamespace(
        product_design=types.SimpleNamespace(
            blueprint=types.SimpleNamespace(vertical=vertical)))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A Store the facts builder will read, wherever a test puts a kit in it."""
    root = tmp_path / "store"

    def resolve():
        return root if (root / "app" / "blocks").is_dir() else None

    module = pytest.importorskip("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root", resolve)

    def put(kit: str, manifest: dict, questions: dict = None) -> pathlib.Path:
        where = root / "app" / "blocks" / kit
        where.mkdir(parents=True, exist_ok=True)
        (where / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
        if questions is not None:
            (where / "questions.yaml").write_text(
                yaml.safe_dump(questions, allow_unicode=True), encoding="utf-8")
        return where

    return put


OWNER_SHEET = {
    "kit": "fitout",
    "title": "Fit-Out — Questions I Cannot Answer",
    "source_document": "docs/kit_questions/fitout.md",
    "answer_format": ["value", "unit", "quality band", "market", "source", "date",
                      "confirmed or indicative"],
    "sections": {"B": {"title": "Rates"}, "M": {"title": "Incidents"}},
    "questions": [
        {"id": "B.1", "section": "B", "gate": True,
         "text": "Cost per m² all-in by your quality band — basic, standard, premium, "
                 "super-prime — and what you define each band as."},
        {"id": "M.11", "section": "M", "gate": False,
         "text": "What a PM new to fit-out most commonly gets wrong in their first year."},
    ],
}

DERIVED_MANIFEST = {
    "kit": "fitout", "version": 1,
    "quantities": {"rate": {"units": ["currency_per_m2"]}},
    "figures": {"rate": {"value": None, "question": "What is the rate for this platform?"}},
}


def test_the_owners_sheet_is_what_the_model_is_given(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))

    assert "DOMAIN OWNER'S OWN question sheet" in facts
    assert "docs/kit_questions/fitout.md" in facts
    # The owner's wording, verbatim, and the id so an answer can find its question.
    assert "[B.1]" in facts
    assert "basic, standard, premium, super-prime" in facts
    # And never the derived one when a sheet exists.
    assert "What is the rate for this platform?" not in facts


def test_the_model_is_told_the_fields_an_answer_must_carry(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    for field in ("quality_band", "market", "confirmed_or_indicative"):
        assert field in facts
    assert "cannot be cited" in facts


def test_only_gating_questions_are_offered_to_ask(store):
    """The GAP questions are worth having and are not what a build stops for. A
    model that asks "what does a new PM get wrong" during a build is spending a
    bounded question round on something that blocks nothing."""
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "2 questions, 1 of them gating" in facts
    assert "[M.11]" not in facts


def test_an_unmarked_question_is_counted_as_gating(store):
    sheet = dict(OWNER_SHEET, questions=[
        {"id": "1.1", "section": "B", "gate": None, "text": "An unmarked question."}])
    store("fitout", DERIVED_MANIFEST, sheet)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "1 questions, 1 of them gating" in facts


def test_the_model_is_told_never_to_invent_a_value_or_claim_one_is_held(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "NEVER invent a value" in facts
    assert "never tell the user a figure is in hand because the question was asked" in facts
    assert "not a stub" in facts


def test_a_derived_question_is_labelled_derived(store):
    """A model that presents "what is the rate?" as the domain's own question
    invites an answer that no rule can then use."""
    store("fitout", DERIVED_MANIFEST)  # no sheet
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "NO owner question sheet" in facts
    assert "DERIVED" in facts
    assert "not the domain owner's own wording" in facts
    assert "What is the rate for this platform?" in facts


def test_a_vertical_with_no_kit_does_not_let_the_model_claim_gating(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("no_such_vertical"))
    assert "none matched" in facts
    assert "do not claim" in facts


def test_a_kit_declaring_no_figures_is_never_reported_as_all_answered(store):
    """The first version of this said "every figure already answered" for a kit
    that declared none at all -- the most misleading thing it could have said."""
    store("fitout", {"kit": "fitout", "version": 1,
                     "quantities": {"rate": {"units": ["currency_per_m2"]}}})
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "NO question list" in facts
    assert "refuse every figure" in facts
    assert "already answered" not in facts


def test_an_unreachable_store_says_so_rather_than_promising_a_gate(store, monkeypatch):
    module = pytest.importorskip("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root", lambda: None)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "Store unreachable" in facts


def test_the_facts_never_raise_into_the_chat_turn(store, monkeypatch):
    """Kit facts are context. A broken Store must not take the Floor down with it."""
    module = pytest.importorskip("app.factory.blocks_source")

    def boom():
        raise RuntimeError("the Store exploded")

    monkeypatch.setattr(module, "resolve_blocks_root", boom)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "unavailable" in facts
