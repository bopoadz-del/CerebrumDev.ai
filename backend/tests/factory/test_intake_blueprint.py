"""INTAKE CHAT → intake_blueprint.v1. Lettings golden reconstructs honestly."""

from __future__ import annotations

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import brief_fingerprint, compile_brief
from app.factory.build.intake_blueprint import (
    SCHEMA_VERSION,
    intake_capability_ids,
    load_lettings_golden_chat,
    load_schema,
    reconstruct_intake_from_chat,
    render_plain_language,
    validate_intake,
)
from app.factory.product_architect import (
    draft_blueprint_from_brief,
    plan_blueprint,
)

ROOT = Path(__file__).resolve().parents[3]
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"
LIVE_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


def test_schema_is_the_collector_contract():
    schema = load_schema()
    assert schema["title"] == "intake_blueprint.v1"
    required = set(schema["required"])
    assert {
        "vertical",
        "capabilities",
        "roles",
        "users",
        "data_sources",
        "integrations",
        "constraints",
        "done_when",
    } <= required


def test_lettings_golden_chat_reconstructs_the_same_roster():
    """Proof the compiler is honest: golden session chat → golden YAML roster."""
    chat = load_lettings_golden_chat()
    intake = reconstruct_intake_from_chat(chat["turns"], use_llm=False)
    validate_intake(intake)
    assert intake["schema_version"] == SCHEMA_VERSION
    assert set(intake_capability_ids(intake)) == LIVE_CAPS
    assert intake["vertical"]["value"] == "residential_lettings"
    assert intake["vertical"]["source_turn"] == 1
    for cap in intake["capabilities"]:
        assert cap["source_turn"] == 1
        assert cap["customer_words"]
        assert cap["id"] in LIVE_CAPS


def test_lettings_reconstructed_chat_compiles_same_as_golden_yaml():
    chat = load_lettings_golden_chat()
    intake = reconstruct_intake_from_chat(chat["turns"], use_llm=False)
    from_chat = draft_blueprint_from_brief(chat["turns"][0]["text"], use_llm=False)
    golden = load_blueprint(LETTINGS)
    compiled_chat = compile_brief(
        from_chat, plan_blueprint(from_chat), intake=intake
    )
    compiled_yaml = compile_brief(golden, plan_blueprint(golden))
    assert brief_fingerprint(compiled_chat)["capabilities"] == brief_fingerprint(
        compiled_yaml
    )["capabilities"]
    assert brief_fingerprint(compiled_chat)["inventory"] == brief_fingerprint(
        compiled_yaml
    )["inventory"]
    assert compiled_chat.missing_reuse == []
    assert compiled_yaml.missing_reuse == []
    assert compiled_chat.template_revision == compiled_yaml.template_revision


def test_plain_language_names_done_when_and_approve():
    chat = load_lettings_golden_chat()
    intake = reconstruct_intake_from_chat(chat["turns"], use_llm=False)
    prose = render_plain_language(intake)
    assert "Residential Lettings" in prose
    assert "Done when:" in prose
    assert "Approve the feature list" in prose
