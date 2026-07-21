"""Adapt TEKsystems hat/agent manifest patterns into product-neutral hats.

TEKsystems uses ``retail.base`` + ``retail.hat.*`` manifests. The Factory emits
the same shape for generated products (e.g. Steward) with neutralized ids:
``{vertical}.base`` and ``{vertical}.hat.{discipline}``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.factory.blueprint import ProductBlueprint
from app.factory.planner import ProductPlan

# Capability id → hat discipline (TEK-style specialization)
_CAPABILITY_DISCIPLINE: Dict[str, str] = {
    "estate_registry": "registry",
    "estate_maintenance": "maintenance",
    "evidence_verifier": "evidence",
    "readiness_engine": "readiness",
    "portfolio_rollup": "portfolio",
    "composed_ops_loop": "operations",
    "evidence_capture": "evidence",
    "human_authority_gate": "authority",
    "house_manual_sop": "house_manual",
    "vendor_budget": "vendors",
    "preventive_maintenance": "maintenance",
    "staff_scheduling": "staff",
    "principal_dashboard": "principal",
    "property_onboarding": "onboarding",
    "dual_rag_sop": "rag_sop",
    "dual_rag_estate_docs": "rag_estate",
    "audit_trail": "audit",
    "health_surface": "platform",
}


def discipline_for(capability_id: str) -> str:
    if capability_id in _CAPABILITY_DISCIPLINE:
        return _CAPABILITY_DISCIPLINE[capability_id]
    return capability_id.replace("-", "_")


def build_hat_manifests(blueprint: ProductBlueprint, plan: ProductPlan) -> List[Dict[str, Any]]:
    """Return base + hat manifests adapted from TEK patterns for this plan."""
    vertical = blueprint.vertical.replace("-", "_")
    base_id = f"{vertical}.base"
    action_ids = [
        f"{vertical}.{c.capability_id.replace('-', '_')}" for c in plan.capabilities
    ]

    base: Dict[str, Any] = {
        "manifest_version": "1.0.0",
        "agent_id": base_id,
        "kind": "base",
        "display_name": f"{blueprint.product_name} Base Agent",
        "discipline": "base",
        "extends": None,
        "summary": (
            "Shared product cortex adapted from TEKsystems hat patterns: trusted "
            "context, ActionRegistry awareness, honesty policies. Discipline hats "
            "specialize this base — they never replace it."
        ),
        "activation": {
            "mode": "auto",
            "default_enabled": True,
            "triggers": [{"type": "intent", "value": "unknown"}],
            "put_off_by": [f"disable:{base_id}"],
        },
        "context_sources": [
            {
                "source_id": "project_docs",
                "kind": "project_documents",
                "description": "Tenant/project documents via hybrid retrieval",
            }
        ],
        "allowed_actions": action_ids,
        "handoffs": [],
        "memory": {
            "scope": "tenant_project",
            "save": [],
            "pii_policy": "never_store_pii",
        },
        "human_authority": blueprint.human_authority,
        "tags": ["base", "factory-generated", vertical],
    }

    hats: List[Dict[str, Any]] = [base]
    seen_disciplines: set[str] = set()

    for cap in plan.capabilities:
        discipline = discipline_for(cap.capability_id)
        if discipline in seen_disciplines or discipline == "base":
            continue
        seen_disciplines.add(discipline)
        hat_id = f"{vertical}.hat.{discipline}"
        action_id = f"{vertical}.{cap.capability_id.replace('-', '_')}"
        hats.append(
            {
                "manifest_version": "1.0.0",
                "agent_id": hat_id,
                "kind": "hat",
                "display_name": f"{blueprint.product_name} {discipline.replace('_', ' ').title()} Hat",
                "discipline": discipline,
                "extends": base_id,
                "summary": (
                    f"Specialized hat for capability '{cap.capability_id}' "
                    f"(strategy={cap.strategy}). Adapted from TEKsystems retail "
                    f"hat pattern; estate/product-neutral."
                ),
                "activation": {
                    "mode": "hybrid",
                    "default_enabled": True,
                    "triggers": [
                        {"type": "intent", "value": discipline},
                        {"type": "capability", "value": cap.capability_id},
                    ],
                    "put_off_by": [f"disable:{hat_id}"],
                },
                "allowed_actions": [action_id],
                "block_ids": list(cap.block_ids),
                "strategy": cap.strategy,
                "handoffs": [],
                "memory": {
                    "scope": "tenant_project",
                    "save": [],
                    "pii_policy": "never_store_pii",
                },
                "human_authority": blueprint.human_authority,
                "tags": ["hat", "factory-generated", vertical, discipline],
            }
        )

    # Wire simple handoffs along composed capabilities
    by_disc = {h["discipline"]: h for h in hats if h["kind"] == "hat"}
    if "operations" in by_disc and "registry" in by_disc:
        by_disc["operations"].setdefault("handoffs", []).append(
            {
                "to_agent_id": by_disc["registry"]["agent_id"],
                "when": "needs_registry_detail",
            }
        )
    if "operations" in by_disc and "readiness" in by_disc:
        by_disc["operations"].setdefault("handoffs", []).append(
            {
                "to_agent_id": by_disc["readiness"]["agent_id"],
                "when": "needs_readiness_gate",
            }
        )

    return hats


def build_workflows(blueprint: ProductBlueprint, plan: ProductPlan) -> List[Dict[str, Any]]:
    """Emit workflow specs composing planned capabilities (Factory-owned)."""
    vertical = blueprint.vertical.replace("-", "_")
    workflows: List[Dict[str, Any]] = []

    cap_ids = {c.capability_id for c in plan.capabilities}
    if {"estate_registry", "estate_maintenance", "readiness_engine"} <= cap_ids:
        workflows.append(
            {
                "workflow_id": f"{vertical}.ops_loop",
                "name": "Estate operations loop",
                "description": "Compose registry → maintenance → readiness with human authority gate.",
                "steps": [
                    {"capability_id": "estate_registry", "role": "inventory"},
                    {"capability_id": "estate_maintenance", "role": "plan_work"},
                    {"capability_id": "readiness_engine", "role": "gate"},
                    {
                        "capability_id": "human_authority_gate",
                        "role": "confirm",
                        "required": blueprint.human_authority,
                    },
                ],
            }
        )
    if {"evidence_capture", "evidence_verifier"} <= cap_ids or "evidence_verifier" in cap_ids:
        workflows.append(
            {
                "workflow_id": f"{vertical}.evidence_chain",
                "name": "Evidence capture and verify",
                "description": "Capture artifacts then verify without PII in learning.",
                "steps": [
                    {"capability_id": "evidence_capture", "role": "capture"},
                    {"capability_id": "evidence_verifier", "role": "verify"},
                ],
            }
        )
    if "portfolio_rollup" in cap_ids:
        workflows.append(
            {
                "workflow_id": f"{vertical}.portfolio_rollup",
                "name": "Portfolio rollup",
                "description": "Roll up readiness and maintenance across properties.",
                "steps": [{"capability_id": "portfolio_rollup", "role": "aggregate"}],
            }
        )
    if "preventive_maintenance" in cap_ids:
        workflows.append(
            {
                "workflow_id": f"{vertical}.preventive_maintenance",
                "name": "Preventive maintenance",
                "description": "Asset register → calendar → work orders → escalation.",
                "steps": [
                    {"capability_id": "estate_registry", "role": "assets"},
                    {"capability_id": "preventive_maintenance", "role": "schedule"},
                    {"capability_id": "principal_dashboard", "role": "escalate_if_overdue"},
                ],
            }
        )
    if "property_onboarding" in cap_ids:
        workflows.append(
            {
                "workflow_id": f"{vertical}.property_onboarding",
                "name": "New property onboarding",
                "description": "Template-driven framework application to a new asset.",
                "steps": [
                    {"capability_id": "property_onboarding", "role": "apply_template"},
                    {"capability_id": "readiness_engine", "role": "gate"},
                    {"capability_id": "human_authority_gate", "role": "confirm"},
                ],
            }
        )
    if {"dual_rag_sop", "dual_rag_estate_docs"} <= cap_ids:
        workflows.append(
            {
                "workflow_id": f"{vertical}.dual_rag_query",
                "name": "Dual RAG query",
                "description": "Query Layer 1 SOP corpus and Layer 2 estate documents separately with citations.",
                "steps": [
                    {"capability_id": "dual_rag_sop", "role": "layer_1"},
                    {"capability_id": "dual_rag_estate_docs", "role": "layer_2"},
                ],
            }
        )

    if not workflows:
        workflows.append(
            {
                "workflow_id": f"{vertical}.capability_sequence",
                "name": "Capability sequence",
                "description": "Default linear workflow over planned capabilities.",
                "steps": [
                    {"capability_id": c.capability_id, "role": "execute"}
                    for c in plan.capabilities
                ],
            }
        )
    return workflows
