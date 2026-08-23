"""S2: :latest fails, unverifiable tags fail, invented block ids fail."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.supply_chain import (
    PYTHON_312_SLIM_FROM,
    SupplyChainError,
    assert_generated_dockerfile,
    assert_known_block_ids,
    findings_for_image_ref,
    known_factory_block_ids,
    redact_unpinned_images,
    scan_dockerfile,
)


def test_latest_is_not_a_pin():
    findings = findings_for_image_ref("ghcr.io/cerebrum-blocks/capture:latest", loc="x")
    assert findings and ":latest" in findings[0]


def test_unpinned_tag_is_not_a_pin():
    findings = findings_for_image_ref("python:3.12-slim", loc="Dockerfile")
    assert findings and "not digest-pinned" in findings[0]


def test_hub_digest_is_accepted():
    assert findings_for_image_ref(PYTHON_312_SLIM_FROM, loc="Dockerfile") == []


def test_generated_dockerfile_rejects_latest():
    with pytest.raises(SupplyChainError, match=":latest"):
        assert_generated_dockerfile("FROM python:latest\n")


def test_generated_dockerfile_rejects_floating_tag():
    with pytest.raises(SupplyChainError, match="not digest-pinned"):
        assert_generated_dockerfile("FROM python:3.12-slim\n")


def test_role_runner_dockerfile_is_digest_pinned():
    from app.factory.build.roles import _render_dockerfile

    text = _render_dockerfile()
    assert PYTHON_312_SLIM_FROM in text
    assert ":latest" not in text
    assert scan_dockerfile(text) == []


def test_unknown_block_id_is_refused():
    with pytest.raises(SupplyChainError, match="do not invent"):
        assert_known_block_ids(["analytics", "not_a_real_block_zzz"])


def test_vendor_mirror_ids_are_known():
    known = known_factory_block_ids()
    assert "analytics" in known
    assert "dashboard" in known
    assert "chat" not in known, "do not invent chat; it is upstream in Cerebrum-Blocks"


def test_redact_unpinned_block_image(tmp_path: Path):
    path = tmp_path / "block.json"
    path.write_text(
        '{"id": "capture", "execution": {"image": "ghcr.io/cerebrum-blocks/capture:latest"}}\n',
        encoding="utf-8",
    )
    assert redact_unpinned_images(path)
    text = path.read_text(encoding="utf-8")
    assert ":latest" not in text
    assert "refused_unverified" in text
