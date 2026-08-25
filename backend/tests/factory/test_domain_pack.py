"""S3: dealership Domain Pack against DOMAIN_PACK_FIELDS (15)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.domain_pack import (
    DOMAIN_ID,
    EMITTER_ID,
    PACK_REL,
    DomainPackError,
    as_delivery_domain_pack,
    assert_pack,
    canonical_fingerprint,
    dealership_domain_pack,
    evaluate_domain_pack,
    fingerprint_disagreements,
    is_empty_pack,
    load_emitted_pack,
    load_lotdesk_pack,
    reject_lotdesk_pack,
    render_dealership_brief,
    reread_matches,
    write_reread_twin,
)
from app.factory.build.preflight import write_evidence
from app.factory.build.roles import _coder_route_body
from app.factory.build.runner import RoleRunner
from app.factory.delivery_standard import DOMAIN_PACK_FIELDS

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_numbered_15_field_contract_is_delivery_standard():
    assert len(DOMAIN_PACK_FIELDS) == 15
    assert DOMAIN_PACK_FIELDS == (
        "domain_purpose",
        "primary_users",
        "required_roles",
        "required_product_modules",
        "core_business_workflows",
        "authoritative_calculations",
        "domain_rules",
        "high_impact_actions",
        "prohibited_autonomous_actions",
        "data_sources",
        "required_connectors",
        "required_exports",
        "security_regulatory_rules",
        "demo_data_requirements",
        "domain_acceptance_conditions",
    )


def test_dealership_pack_exists_with_all_15_fields_kernel_bound():
    pack = dealership_domain_pack()
    assert_pack(pack)
    assert pack["domain_id"] == DOMAIN_ID
    assert list(pack["fields"]) == list(DOMAIN_PACK_FIELDS)
    for name in DOMAIN_PACK_FIELDS:
        item = pack["fields"][name]
        assert item["value"], name
        kernel = item["kernel"]
        assert kernel["action_ids"], name
        assert kernel["contracts"], name
        assert kernel["entry"].endswith("execute_action")


def test_missing_field_fails_the_gate():
    pack = dealership_domain_pack()
    fields = dict(pack["fields"])
    fields.pop("authoritative_calculations")
    bad = dict(pack)
    bad["fields"] = fields
    with pytest.raises(DomainPackError, match="authoritative_calculations"):
        assert_pack(bad)


def test_blank_value_fails_the_gate():
    pack = dealership_domain_pack()
    pack["fields"]["domain_purpose"]["value"] = "   "
    with pytest.raises(DomainPackError, match="domain_purpose"):
        assert_pack(pack)


def test_markdown_essay_without_kernel_binding_fails():
    pack = dealership_domain_pack()
    pack["fields"]["domain_rules"]["kernel"] = {}
    with pytest.raises(DomainPackError, match="domain_rules.kernel"):
        assert_pack(pack)


def test_empty_pack_is_rejected():
    with pytest.raises(DomainPackError, match="empty"):
        assert_pack({})
    assert is_empty_pack({}) is True
    assert is_empty_pack({"fields": {}}) is True


def test_lotdesk_class_empty_pack_is_rejected():
    pack = load_lotdesk_pack()
    assert is_empty_pack(pack) is True
    result = reject_lotdesk_pack()
    assert result["ok"] is False
    assert result["empty"] is True
    assert result["lotdesk"] == "fixture only; not patched"
    with pytest.raises(DomainPackError):
        assert_pack(pack)


def test_pack_renders_through_delivery_standard():
    brief = render_dealership_brief()
    flat = as_delivery_domain_pack(dealership_domain_pack())
    assert "PLATFORM_NAME: Cerebrum Dealership" in brief
    assert "[INSERT" not in brief
    for name in DOMAIN_PACK_FIELDS:
        assert name in flat


def test_role_runner_emits_domain_pack(tmp_path: Path):
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    pack_path = out / PACK_REL
    assert pack_path.is_file()
    pack = load_emitted_pack(out)
    assert_pack(pack)
    assert pack["fields_order"] == list(DOMAIN_PACK_FIELDS)
    assert set(pack["fields"]) == set(DOMAIN_PACK_FIELDS)


def test_coder_route_body_stays_none_on_s3_path():
    assert _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_evaluate_domain_pack_and_reread(tmp_path: Path):
    result = evaluate_domain_pack()
    assert result["stage"] == "S3"
    assert result["emitter"] == EMITTER_ID
    assert result["PILOT_READY"] is False
    assert result["field_count"] == 15
    assert result["fields"] == list(DOMAIN_PACK_FIELDS)
    assert result["pass_criteria"]["all_15_fields_present"] is True
    assert result["pass_criteria"]["missing_field_fails_gate"] is True
    assert result["pass_criteria"]["lotdesk_empty_pack_rejected"] is True
    assert result["pass_criteria"]["coder_route_body_is_None"] is True
    assert result["ok"] is True
    assert result["verdict"] == "PASS"
    dest = tmp_path / "S3_domain_pack.json"
    write_evidence(dest, result)
    twin_path = write_reread_twin(dest, result)
    written = json.loads(dest.read_text(encoding="utf-8"))
    twin = json.loads(twin_path.read_text(encoding="utf-8"))
    assert twin["disagreements"] == []
    assert reread_matches(written, twin) is True
    assert canonical_fingerprint(result) == canonical_fingerprint(
        evaluate_domain_pack()
    )
    other = evaluate_domain_pack()
    other["git_sha"] = "0" * 40
    assert "git_sha" in fingerprint_disagreements(result, other)
