"""Action APIs, orchestrator anti-hijack routing, audit, and E2E acceptance."""

from __future__ import annotations

import pytest

from app.retailops import bootstrap
from app.retailops.db import session_scope
from app.retailops.kit.contract import ActionContext, ActionStatus
from app.retailops.orchestrator import run_orchestrator
from app.retailops.service import build_registry

MANAGER = "u_ops_manager"
VIEWER = "u_viewer"
TENANT = "northstar"


def _headers(user, project_id):
    return {"X-User-Id": user, "X-Tenant-Id": TENANT, "X-Project-Id": project_id}


# ---------------------------------------------------------------------------
# Action discovery + execution APIs
# ---------------------------------------------------------------------------

def test_list_and_get_actions(client, loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    r = client.get("/v1/actions", headers=_headers(MANAGER, pid))
    ids = {a["action_id"] for a in r.json()["actions"]}
    assert ids == {
        "retail.generate_operations_brief",
        "retail.analyse_inventory_risk",
        "retail.check_promotion_compliance",
    }
    r2 = client.get("/v1/actions/retail.analyse_inventory_risk", headers=_headers(MANAGER, pid))
    assert r2.status_code == 200
    assert "input_schema" in r2.json()
    assert "handler" not in r2.json()  # handler is never serialized


def test_execute_unsupported(client, loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    r = client.post(
        "/v1/actions/retail.no_such_action/execute",
        headers=_headers(MANAGER, pid), json={"arguments": {}},
    )
    assert r.json()["status"] == "unsupported"


def test_execute_permission_denied(client, loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    r = client.post(
        "/v1/actions/retail.analyse_inventory_risk/execute",
        headers=_headers(VIEWER, pid), json={"arguments": {"rows": [{"SKU": "X", "On Hand": 1}]}},
    )
    assert r.json()["status"] == "permission_denied"


def test_execute_dependency_required_on_empty_project(client, loaded_projects):
    with session_scope() as s:
        empty = bootstrap.ensure_project(s, tenant_id=TENANT, canonical_name="Northstar Empty Region").id
    r = client.post(
        "/v1/actions/retail.analyse_inventory_risk/execute",
        headers=_headers(MANAGER, empty), json={"arguments": {}},
    )
    body = r.json()
    assert body["status"] == "dependency_required"
    assert body["dependencies"][0]["kind"] == "inventory_data"


def test_execute_success_and_audit_persisted(client, loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    r = client.post(
        "/v1/actions/retail.analyse_inventory_risk/execute",
        headers=_headers(MANAGER, pid), json={"arguments": {}},
    )
    body = r.json()
    assert body["status"] == "success"
    assert body["output"]["summary"]["skus_analysed"] > 0
    # BEV-2201 on_hand 4 <= reorder 20 -> stockout
    skus = {x["sku"] for x in body["output"]["stockout_risks"]}
    assert "BEV-2201" in skus

    audit = client.get("/v1/audit", headers=_headers(MANAGER, pid)).json()["runs"]
    assert any(row["request_id"] == body["request_id"] for row in audit)


def test_reserved_argument_override_ignored(client, loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    r = client.post(
        "/v1/actions/retail.generate_operations_brief/execute",
        headers=_headers(MANAGER, pid),
        json={"arguments": {"period": "daily", "tenant_id": "attacker", "permissions": ["admin"]}},
    )
    body = r.json()
    assert body["status"] == "success"
    assert any("reserved argument" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# Orchestrator anti-hijack routing (Part 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,expected_route",
    [
        ("What is the escalation process for a stock discrepancy?", "rag"),
        ("Generate today's operations brief", "action"),
        ("Which supplier notice affects chilled products?", "rag"),
        ("Analyse inventory risk", "action"),
    ],
)
async def test_orchestrator_routing(loaded_projects, question, expected_route):
    pid = loaded_projects["Northstar Riyadh Region"]
    ctx = ActionContext(
        user_id=MANAGER, tenant_id=TENANT, organisation_id=TENANT, project_id=pid,
        permissions=["retail:read", "retail:analyse"], allowed_domains=["retail"],
    )
    with session_scope() as s:
        out = await run_orchestrator(session=s, context=ctx, question=question)
    assert out["route"] == expected_route


@pytest.mark.asyncio
async def test_factual_question_uses_rag_with_citations(loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    ctx = ActionContext(
        user_id=MANAGER, tenant_id=TENANT, organisation_id=TENANT, project_id=pid,
        permissions=["retail:read", "retail:analyse"], allowed_domains=["retail"],
    )
    with session_scope() as s:
        out = await run_orchestrator(
            session=s, context=ctx,
            question="What is the escalation process for a stock discrepancy?",
        )
    assert out["route"] == "rag"
    assert out["citations"]
    assert out["answer"]


@pytest.mark.asyncio
async def test_unknown_request_never_fabricates(loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    ctx = ActionContext(
        user_id=MANAGER, tenant_id=TENANT, organisation_id=TENANT, project_id=pid,
        permissions=["retail:read"], allowed_domains=["retail"],
    )
    with session_scope() as s:
        out = await run_orchestrator(
            session=s, context=ctx, question="Please book me a flight to the moon."
        )
    # Never returns an action result for an unknown request.
    assert out["action_result"] is None


# ---------------------------------------------------------------------------
# Cross-project evidence isolation (negative)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_project_cannot_retrieve_other_evidence(loaded_projects):
    riyadh = loaded_projects["Northstar Riyadh Region"]
    melbourne = loaded_projects["Northstar Melbourne Region"]
    assert riyadh != melbourne
    ctx = ActionContext(
        user_id=MANAGER, tenant_id=TENANT, organisation_id=TENANT, project_id=melbourne,
        permissions=["retail:read"], allowed_domains=["retail"],
    )
    with session_scope() as s:
        out = await run_orchestrator(
            session=s, context=ctx,
            question="What did the Al-Waha chilled dairy supplier notice say?",
        )
    # Riyadh-only supplier ("Al-Waha") must not surface in Melbourne citations.
    joined = " ".join(c.get("excerpt", "") for c in out["citations"]).lower()
    assert "al-waha" not in joined


# ---------------------------------------------------------------------------
# End-to-end acceptance sequence
# ---------------------------------------------------------------------------

def test_end_to_end_three_actions(client, loaded_projects):
    pid = loaded_projects["Northstar Riyadh Region"]
    H = _headers(MANAGER, pid)

    brief = client.post(
        "/v1/actions/retail.generate_operations_brief/execute",
        headers=H, json={"arguments": {"period": "daily"}},
    ).json()
    assert brief["status"] == "success"
    assert brief["output"]["priority_actions"]

    inv = client.post(
        "/v1/actions/retail.analyse_inventory_risk/execute",
        headers=H, json={"arguments": {}},
    ).json()
    assert inv["status"] == "success"

    promo = client.post(
        "/v1/actions/retail.check_promotion_compliance/execute",
        headers=H, json={"arguments": {}},
    ).json()
    assert promo["status"] == "success"
    assert promo["output"]["rule_results"]

    overview = client.get("/v1/overview", headers=H).json()
    assert overview["document_count"] >= 6
