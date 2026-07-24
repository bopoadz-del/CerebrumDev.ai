"""Tests verifying Cerebrum-Blocks automotive kit hardening."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def blocks_path() -> Path:
    import os

    path = Path(os.getenv("CEREBRUM_BLOCKS_PATH", Path.home() / "Cerebrum-Blocks"))
    return path / "block_store" / "kits" / "automotive"


def test_automotive_kit_manifest_is_valid_json(blocks_path: Path) -> None:
    if not blocks_path.exists():
        pytest.skip(reason=f"Cerebrum-Blocks automotive kit not found at {blocks_path}")
    manifest_path = blocks_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["id"] == "automotive"
    assert manifest["version"] == "2.0.0"


def test_automotive_kit_has_rag_section(blocks_path: Path) -> None:
    if not blocks_path.exists():
        pytest.skip(reason=f"Cerebrum-Blocks automotive kit not found at {blocks_path}")
    manifest = json.loads((blocks_path / "manifest.json").read_text(encoding="utf-8"))
    assert "rag" in manifest
    assert manifest["rag"]["pack"] == "automotive_core_rag_v1"
    assert manifest["rag"]["source_manifest"] == "source_manifest.json"


def test_automotive_kit_source_manifest_exists(blocks_path: Path) -> None:
    if not blocks_path.exists():
        pytest.skip(reason=f"Cerebrum-Blocks automotive kit not found at {blocks_path}")
    source_manifest = blocks_path / "source_manifest.json"
    assert source_manifest.exists()
    data = json.loads(source_manifest.read_text(encoding="utf-8"))
    assert data["pack_id"] == "automotive_core_rag_v1"
    families = {s["source_family"] for s in data["source_families"]}
    assert {"recall", "investigation", "complaint", "safety_rating", "vehicle_identity"} <= families


def test_automotive_kit_schemas_exist(blocks_path: Path) -> None:
    if not blocks_path.exists():
        pytest.skip(reason=f"Cerebrum-Blocks automotive kit not found at {blocks_path}")
    schemas_dir = blocks_path / "schemas"
    expected = {"recall.json", "complaint.json", "investigation.json", "safety_rating.json", "vehicle_identity.json"}
    found = {p.name for p in schemas_dir.glob("*.json")}
    assert expected <= found


def test_automotive_kit_prompt_exists(blocks_path: Path) -> None:
    if not blocks_path.exists():
        pytest.skip(reason=f"Cerebrum-Blocks automotive kit not found at {blocks_path}")
    prompt = blocks_path / "prompts" / "assistant_v1.txt"
    assert prompt.exists()
    text = prompt.read_text(encoding="utf-8")
    assert "retrieved evidence" in text.lower()


def test_automotive_kit_evaluation_questions_exist(blocks_path: Path) -> None:
    if not blocks_path.exists():
        pytest.skip(reason=f"Cerebrum-Blocks automotive kit not found at {blocks_path}")
    questions = blocks_path / "evaluation" / "development_seed.jsonl"
    assert questions.exists()
    lines = questions.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    for line in lines:
        item = json.loads(line)
        assert "question_id" in item
        assert "category" in item
        assert "question" in item
        assert item.get("development_seed") is True
