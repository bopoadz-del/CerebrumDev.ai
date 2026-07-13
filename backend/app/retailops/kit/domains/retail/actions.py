"""Retail domain actions for the RetailOps pilot.

Three pilot actions, all deterministic (no LLM calls in the handler body):

* ``retail.generate_operations_brief``
* ``retail.analyse_inventory_risk``
* ``retail.check_promotion_compliance``

Handlers receive a trusted :class:`ActionContext` and already-retrieved
records/rows from the host runtime. They never fabricate results: when the
required inputs are absent they return ``dependency_required`` with a concrete
dependency describing what is needed.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.retailops.kit.contract.models import (
    ActionContext,
    ActionDependency,
    ActionEvidence,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.retailops.kit.domains.retail import RETAIL_KIT_VERSION
from app.retailops.kit.domains.retail import schemas as S

_PERM_READ = "retail:read"
_PERM_ANALYSE = "retail:analyse"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_evidence(rec: Dict[str, Any]) -> ActionEvidence:
    return ActionEvidence(
        source_id=rec.get("source_id"),
        document_id=rec.get("document_id"),
        filename=rec.get("filename"),
        page=rec.get("page"),
        sheet=rec.get("sheet"),
        row_start=rec.get("row_start"),
        row_end=rec.get("row_end"),
        chunk_id=rec.get("chunk_id"),
        excerpt=(rec.get("text") or "")[:280] or None,
        score=rec.get("score"),
    )


def _classify_record(text: str) -> List[str]:
    """Return the operational signal categories a record touches."""
    t = text.lower()
    cats: List[str] = []
    if any(k in t for k in ["stock", "sku", "inventory", "on hand", "reorder", "stockout", "discrepancy", "variance", "count"]):
        cats.append("inventory")
    if any(k in t for k in ["promotion", "promo", "display", "campaign", "discount", "endcap", "signage", "planogram"]):
        cats.append("promotion")
    if any(k in t for k in ["supplier", "vendor", "delivery", "shipment", "notice", "recall", "lead time", "chilled", "backorder"]):
        cats.append("supplier")
    if any(k in t for k in ["incident", "injury", "accident", "safety", "escalat", "hazard", "spill", "theft"]):
        cats.append("incident")
    return cats


def _first_sentence(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    for sep in (". ", "! ", "? ", "\n"):
        idx = text.find(sep)
        if 0 < idx < limit:
            return text[: idx + 1].strip()
    return text[:limit].strip()


# ---------------------------------------------------------------------------
# A. generate_operations_brief
# ---------------------------------------------------------------------------

async def generate_operations_brief(
    context: ActionContext, arguments: Dict[str, Any]
) -> ActionOutcome:
    records: List[Dict[str, Any]] = [
        r for r in (arguments.get("records") or []) if (r.get("text") or "").strip()
    ]
    if not records:
        return ActionOutcome.dependency(
            [
                ActionDependency(
                    kind="operational_records",
                    description=(
                        "At least one operational record (incident report, supplier "
                        "notice, inventory record or merchandising instruction) is "
                        "required to generate an operations brief."
                    ),
                    hint="Ingest project documents or provide records for the requested period.",
                )
            ],
            metadata={"period": arguments.get("period")},
        )

    signals: Dict[str, List[Dict[str, Any]]] = {
        "inventory": [],
        "promotion": [],
        "supplier": [],
        "incident": [],
    }
    evidence: List[ActionEvidence] = []
    for rec in records:
        text = rec["text"]
        ev = _record_evidence(rec)
        evidence.append(ev)
        entry = {
            "summary": _first_sentence(text),
            "source": rec.get("filename") or rec.get("source_id") or rec.get("document_id"),
            "document_id": rec.get("document_id"),
            "page": rec.get("page"),
        }
        for cat in _classify_record(text) or ["incident"]:
            if cat in signals:
                signals[cat].append(entry)

    priority_actions: List[Dict[str, Any]] = []
    if signals["incident"]:
        priority_actions.append(
            {"priority": "high", "action": "Review and close open store incidents", "count": len(signals["incident"])}
        )
    if signals["inventory"]:
        priority_actions.append(
            {"priority": "high", "action": "Investigate inventory discrepancies / stock risks", "count": len(signals["inventory"])}
        )
    if signals["supplier"]:
        priority_actions.append(
            {"priority": "medium", "action": "Action outstanding supplier notices", "count": len(signals["supplier"])}
        )
    if signals["promotion"]:
        priority_actions.append(
            {"priority": "medium", "action": "Verify promotion / display compliance", "count": len(signals["promotion"])}
        )

    missing_information: List[str] = []
    for cat, label in [
        ("inventory", "inventory records"),
        ("promotion", "promotion / merchandising instructions"),
        ("supplier", "supplier notices"),
    ]:
        if not signals[cat]:
            missing_information.append(f"No {label} found for this period.")

    scope = arguments.get("store") or arguments.get("region") or "all locations"
    period = arguments.get("period", "daily")
    executive_summary = (
        f"{period.capitalize()} operations brief for {scope}: "
        f"{len(signals['incident'])} incident(s), {len(signals['inventory'])} inventory signal(s), "
        f"{len(signals['supplier'])} supplier signal(s), {len(signals['promotion'])} promotion signal(s) "
        f"across {len(records)} record(s)."
    )

    output = {
        "executive_summary": executive_summary,
        "priority_actions": priority_actions,
        "inventory_signals": signals["inventory"],
        "promotion_signals": signals["promotion"],
        "supplier_signals": signals["supplier"],
        "incidents": signals["incident"],
        "missing_information": missing_information,
        "evidence": [e.to_dict() for e in evidence],
    }
    return ActionOutcome.success(output, evidence=evidence)


# ---------------------------------------------------------------------------
# B. analyse_inventory_risk
# ---------------------------------------------------------------------------

def _collect_rows(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Explicit rows win; otherwise fall back to structured rows carried on
    # records (avoids double-counting when both are supplied by enrichment).
    explicit = list(arguments.get("rows") or [])
    if explicit:
        return explicit
    rows: List[Dict[str, Any]] = []
    for rec in arguments.get("records") or []:
        payload = rec.get("rows") or rec.get("structured")
        if isinstance(payload, list):
            rows.extend(x for x in payload if isinstance(x, dict))
    return rows


async def analyse_inventory_risk(
    context: ActionContext, arguments: Dict[str, Any]
) -> ActionOutcome:
    raw_rows = _collect_rows(arguments)
    normalized = [S.normalize_inventory_row(r) for r in raw_rows]
    usable = [r for r in normalized if r.get("sku") and r.get("on_hand") is not None]

    if not usable:
        return ActionOutcome.dependency(
            [
                ActionDependency(
                    kind="inventory_data",
                    description="inventory CSV/XLSX or structured stock data required",
                    hint="Provide rows containing at least an SKU and on-hand quantity.",
                )
            ],
            metadata={"rows_received": len(raw_rows)},
        )

    thresholds = arguments.get("thresholds") or {}
    slow_days = float(thresholds.get("slow_moving_days", 90))
    overstock_days = float(thresholds.get("overstock_days", 120))
    stockout_days = float(thresholds.get("stockout_days", 7))

    present = S.present_fields([S.normalize_inventory_row(r) for r in raw_rows])
    missing_fields = [
        f
        for f in ["reorder_point", "safety_stock", "average_daily_sales"]
        if f not in present
    ]

    stockout_risks: List[Dict[str, Any]] = []
    overstock_risks: List[Dict[str, Any]] = []
    slow_moving: List[Dict[str, Any]] = []
    anomalies: List[Dict[str, Any]] = []

    for row in usable:
        sku = row["sku"]
        on_hand = row["on_hand"]
        ads = row.get("average_daily_sales")
        rop = row.get("reorder_point")
        safety = row.get("safety_stock")
        item = {"sku": sku, "product_name": row.get("product_name"), "on_hand": on_hand, "store": row.get("store")}

        # Anomalies: negative stock, count variance.
        if on_hand < 0:
            anomalies.append({**item, "issue": "negative_on_hand"})
        last_c = row.get("last_count_quantity")
        cur_c = row.get("current_count_quantity")
        if last_c is not None and cur_c is not None and last_c > 0:
            variance = abs(cur_c - last_c) / last_c
            if variance >= 0.2:
                anomalies.append({**item, "issue": "count_variance", "variance_pct": round(variance * 100, 1)})

        # Stockout risk.
        days_of_supply = None
        if ads and ads > 0:
            days_of_supply = round(on_hand / ads, 1)
        reasons: List[str] = []
        if rop is not None and on_hand <= rop:
            reasons.append("on_hand<=reorder_point")
        if safety is not None and on_hand <= safety:
            reasons.append("on_hand<=safety_stock")
        if days_of_supply is not None and days_of_supply <= stockout_days:
            reasons.append(f"days_of_supply<={stockout_days}")
        if reasons:
            stockout_risks.append({**item, "days_of_supply": days_of_supply, "reasons": reasons})
            continue

        # Overstock / slow-moving.
        if days_of_supply is not None and days_of_supply >= overstock_days:
            overstock_risks.append({**item, "days_of_supply": days_of_supply})
        elif days_of_supply is not None and days_of_supply >= slow_days:
            slow_moving.append({**item, "days_of_supply": days_of_supply})
        elif ads is not None and ads == 0 and on_hand > 0:
            slow_moving.append({**item, "days_of_supply": None, "reason": "no_sales"})

    assumptions = [
        f"days_of_supply = on_hand / average_daily_sales; stockout if <= {stockout_days} days.",
        f"overstock if days_of_supply >= {overstock_days}; slow-moving if >= {slow_days}.",
        "count variance flagged at >= 20% between last and current physical counts.",
    ]

    evidence = [
        _record_evidence(rec)
        for rec in (arguments.get("records") or [])
        if rec.get("text")
    ]

    summary = {
        "skus_analysed": len(usable),
        "stockout_count": len(stockout_risks),
        "overstock_count": len(overstock_risks),
        "slow_moving_count": len(slow_moving),
        "anomaly_count": len(anomalies),
    }

    output = {
        "summary": summary,
        "stockout_risks": stockout_risks,
        "overstock_risks": overstock_risks,
        "slow_moving_items": slow_moving,
        "anomalies": anomalies,
        "assumptions": assumptions,
        "missing_fields": missing_fields,
        "evidence": [e.to_dict() for e in evidence],
    }
    return ActionOutcome.success(output, evidence=evidence)


# ---------------------------------------------------------------------------
# C. check_promotion_compliance
# ---------------------------------------------------------------------------

def _extract_rules(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    for i, rule in enumerate(arguments.get("rules") or []):
        if isinstance(rule, dict) and (rule.get("requirement") or rule.get("text")):
            rules.append(
                {
                    "rule_id": rule.get("rule_id") or rule.get("id") or f"rule_{i+1}",
                    "requirement": rule.get("requirement") or rule.get("text"),
                    "keywords": [k.lower() for k in (rule.get("keywords") or [])],
                    "source": rule.get("source"),
                }
            )
    # Rules may also be supplied as parsed rule records (one requirement per line).
    for rec in arguments.get("rule_records") or []:
        for j, line in enumerate((rec.get("text") or "").splitlines()):
            line = line.strip("-• \t")
            if len(line) < 8:
                continue
            rules.append(
                {
                    "rule_id": f"{rec.get('document_id') or rec.get('filename') or 'rules'}#{j+1}",
                    "requirement": line,
                    "keywords": [],
                    "source": rec.get("filename") or rec.get("document_id"),
                }
            )
    return rules


def _keywords_for(rule: Dict[str, Any]) -> List[str]:
    if rule["keywords"]:
        return rule["keywords"]
    # Derive salient keywords from the requirement text (deterministic).
    stop = {
        "the", "a", "an", "must", "should", "be", "is", "are", "to", "of", "and",
        "or", "in", "on", "at", "for", "with", "all", "any", "each", "that", "this",
        "no", "not", "within", "per", "than", "least", "more", "less",
    }
    words = [w.strip(".,;:()").lower() for w in rule["requirement"].split()]
    salient = [w for w in words if len(w) > 3 and w not in stop]
    return salient[:6]


async def check_promotion_compliance(
    context: ActionContext, arguments: Dict[str, Any]
) -> ActionOutcome:
    rules = _extract_rules(arguments)
    if not rules:
        return ActionOutcome.dependency(
            [
                ActionDependency(
                    kind="promotion_rules",
                    description="promotion rules document required before a compliance conclusion can be drawn",
                    hint="Upload the promotion rules / display standards for this campaign.",
                )
            ],
        )

    evidence_records = [
        r for r in (arguments.get("evidence") or []) if (r.get("text") or "").strip()
    ]
    evidence_text = "\n".join(r["text"] for r in evidence_records).lower()

    rule_results: List[Dict[str, Any]] = []
    compliant: List[Dict[str, Any]] = []
    non_compliant: List[Dict[str, Any]] = []
    missing_evidence: List[Dict[str, Any]] = []

    for rule in rules:
        keywords = _keywords_for(rule)
        if not evidence_records:
            finding = "missing_evidence"
            confidence = 0.0
            ref = None
            explanation = "No campaign/store evidence was supplied to assess this rule."
        else:
            hits = [kw for kw in keywords if kw in evidence_text]
            matched_rec = None
            for rec in evidence_records:
                low = rec["text"].lower()
                if any(kw in low for kw in keywords):
                    matched_rec = rec
                    break
            coverage = (len(hits) / len(keywords)) if keywords else 0.0
            if coverage >= 0.5 and matched_rec is not None:
                finding = "compliant"
                confidence = round(min(1.0, 0.5 + coverage / 2), 2)
                ref = _record_evidence(matched_rec).to_dict()
                explanation = f"Evidence matched {len(hits)}/{len(keywords)} required terms."
            elif coverage > 0:
                finding = "non_compliant"
                confidence = round(0.4 + coverage / 2, 2)
                ref = _record_evidence(matched_rec).to_dict() if matched_rec else None
                explanation = f"Evidence only partially matched ({len(hits)}/{len(keywords)} terms)."
            else:
                finding = "non_compliant"
                confidence = 0.5
                ref = None
                explanation = "No supplied evidence satisfied this rule's requirements."

        result = {
            "rule_id": rule["rule_id"],
            "requirement": rule["requirement"],
            "finding": finding,
            "evidence_reference": ref,
            "confidence": confidence,
            "explanation": explanation,
        }
        rule_results.append(result)
        if finding == "compliant":
            compliant.append(result)
        elif finding == "non_compliant":
            non_compliant.append(result)
        else:
            missing_evidence.append(result)

    summary = {
        "rules_evaluated": len(rules),
        "compliant": len(compliant),
        "non_compliant": len(non_compliant),
        "missing_evidence": len(missing_evidence),
        "overall": "compliant" if not non_compliant and not missing_evidence else "attention_required",
    }

    output = {
        "compliant": compliant,
        "non_compliant": non_compliant,
        "missing_evidence": missing_evidence,
        "rule_results": rule_results,
        "summary": summary,
        "evidence": [_record_evidence(r).to_dict() for r in evidence_records],
    }
    return ActionOutcome.success(output, evidence=[_record_evidence(r) for r in evidence_records])


# ---------------------------------------------------------------------------
# Action specifications (discovered by the registry)
# ---------------------------------------------------------------------------

ACTION_SPECS: List[ActionSpec] = [
    ActionSpec(
        action_id="retail.generate_operations_brief",
        domain="retail",
        name="Generate Operations Brief",
        description="Generate a structured daily or weekly operations brief from available project records.",
        version="1.0.0",
        kit_version=RETAIL_KIT_VERSION,
        input_schema=S.OPERATIONS_BRIEF_INPUT,
        output_schema=S.OPERATIONS_BRIEF_OUTPUT,
        required_context=["tenant_id", "project_id"],
        permissions=[_PERM_READ],
        risk_classification="low",
        read_only=True,
        confirmation_required=False,
        synthesis_instructions=(
            "Summarise the brief for a store/regional manager. Lead with priority "
            "actions and incidents, cite each signal's source, and never invent "
            "figures that are not present in the evidence."
        ),
        evaluation_fixtures=["operations_brief_happy", "operations_brief_no_records"],
        handler=generate_operations_brief,
    ),
    ActionSpec(
        action_id="retail.analyse_inventory_risk",
        domain="retail",
        name="Analyse Inventory Risk",
        description="Analyse structured inventory data for stockout, overstock, slow-moving stock and anomalies.",
        version="1.0.0",
        kit_version=RETAIL_KIT_VERSION,
        input_schema=S.INVENTORY_RISK_INPUT,
        output_schema=S.INVENTORY_RISK_OUTPUT,
        required_context=["tenant_id", "project_id"],
        permissions=[_PERM_ANALYSE],
        risk_classification="low",
        read_only=True,
        confirmation_required=False,
        synthesis_instructions=(
            "Report inventory risks grouped by type with the deterministic figures "
            "provided. State assumptions and missing fields explicitly."
        ),
        evaluation_fixtures=["inventory_risk_happy", "inventory_risk_no_data"],
        handler=analyse_inventory_risk,
    ),
    ActionSpec(
        action_id="retail.check_promotion_compliance",
        domain="retail",
        name="Check Promotion Compliance",
        description="Compare campaign or store evidence against uploaded promotion rules.",
        version="1.0.0",
        kit_version=RETAIL_KIT_VERSION,
        input_schema=S.PROMOTION_COMPLIANCE_INPUT,
        output_schema=S.PROMOTION_COMPLIANCE_OUTPUT,
        required_context=["tenant_id", "project_id"],
        permissions=[_PERM_ANALYSE],
        risk_classification="medium",
        read_only=True,
        confirmation_required=False,
        synthesis_instructions=(
            "For each rule, state the finding (compliant / non-compliant / missing "
            "evidence) with its evidence reference. Never conclude compliance "
            "without a source rule."
        ),
        evaluation_fixtures=["promotion_compliance_happy", "promotion_compliance_no_rules"],
        handler=check_promotion_compliance,
    ),
]
