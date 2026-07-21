#!/usr/bin/env python3
"""Steward pilot certification v2 gate runner.

Emits PASS / FAIL / NOT VERIFIED for each mandatory gate (G1–G14).
Run locally before pushing Factory or generated Steward changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

GATES: List[Dict[str, str]] = [
    {"gate": "G1_factory_determinism", "owner": "Factory"},
    {"gate": "G2_authentication", "owner": "Factory"},
    {"gate": "G3_authorization", "owner": "Factory"},
    {"gate": "G4_isolation", "owner": "Factory"},
    {"gate": "G5_database", "owner": "Factory"},
    {"gate": "G6_rag", "owner": "Factory"},
    {"gate": "G7_store", "owner": "Factory"},
    {"gate": "G8_agents", "owner": "Factory"},
    {"gate": "G9_workflows", "owner": "Factory"},
    {"gate": "G10_resident_engineer", "owner": "Factory"},
    {"gate": "G11_financial", "owner": "Factory"},
    {"gate": "G12_deployment", "owner": "Factory"},
    {"gate": "G13_oracle", "owner": "Factory"},
    {"gate": "G14_documentation", "owner": "Factory"},
]


def _kit_path(*parts: str) -> Path:
    return ROOT / "backend" / "app" / "factory" / "kits" / "private_estate_operations" / Path(*parts)


def _evaluate_gate(gate_id: str) -> Dict[str, Any]:
    auth_py = _kit_path("steward_runtime", "auth.py")
    auth_models = _kit_path("steward_runtime", "auth_models.py")
    probes_py = _kit_path("steward_runtime", "probes.py")
    api_py = _kit_path("steward_runtime", "api.py")
    migration_auth = _kit_path("steward_runtime", "migrations", "versions", "0002_pilot_auth.py")
    gate_script = ROOT / "scripts" / "steward_gate_v2.py"
    oracle_script = ROOT / "scripts" / "steward_oracle_v2.py"
    determinism = ROOT / "artifacts" / "steward_factory_determinism.json"

    if gate_id == "G1_factory_determinism":
        status = "NOT VERIFIED" if not determinism.is_file() else "PASS"
        return {"status": status, "detail": "double-generation artifact absent" if status != "PASS" else "artifact present"}

    if gate_id == "G2_authentication":
        ok = auth_py.is_file() and auth_models.is_file()
        text = auth_py.read_text(encoding="utf-8") if auth_py.is_file() else ""
        has_models = all(x in text for x in ("PilotPrincipal", "PilotAccessToken", "hash_token"))
        status = "PASS" if ok and has_models else "FAIL"
        return {"status": status, "detail": "pilot auth kit present" if status == "PASS" else "missing pilot auth kit"}

    if gate_id == "G3_authorization":
        text = auth_py.read_text(encoding="utf-8") if auth_py.is_file() else ""
        status = "PASS" if "authorize_estate" in text else "FAIL"
        return {"status": status, "detail": "authorize_estate implemented" if status == "PASS" else "authorize_estate missing"}

    if gate_id == "G4_isolation":
        api_text = api_py.read_text(encoding="utf-8") if api_py.is_file() else ""
        removed_defaults = "tenant_a" not in api_text or "_scope" not in api_text
        status = "PASS" if removed_defaults and "get_authenticated_principal" in api_text else "FAIL"
        return {
            "status": status,
            "detail": "header defaults removed; principal auth wired" if status == "PASS" else "caller-controlled defaults remain",
        }

    if gate_id == "G5_database":
        api_text = api_py.read_text(encoding="utf-8") if api_py.is_file() else ""
        protected = "admin_route_disabled" in api_text and migration_auth.is_file()
        status = "PASS" if protected else "FAIL"
        return {
            "status": status,
            "detail": "admin routes gated; auth migration present" if status == "PASS" else "public admin routes or missing migration",
        }

    if gate_id == "G6_rag":
        estate_router = ROOT / "backend" / "app" / "factory" / "kits" / "private_estate_operations" / "estate_kit_router.py"
        text = estate_router.read_text(encoding="utf-8") if estate_router.is_file() else ""
        status = "PASS" if "_legacy_rag_guard" in text else "FAIL"
        return {
            "status": status,
            "detail": "legacy /v1/rag/* guard present" if status == "PASS" else "legacy RAG not gated",
        }

    if gate_id == "G7_store":
        return {"status": "FAIL", "detail": "block runtime execution not wired"}

    if gate_id == "G8_agents":
        return {"status": "FAIL", "detail": "agent runtime certification harness absent"}

    if gate_id == "G9_workflows":
        return {"status": "FAIL", "detail": "approval persistence not implemented"}

    if gate_id == "G10_resident_engineer":
        return {"status": "FAIL", "detail": "HealApproval + simulated heal honesty pending"}

    if gate_id == "G11_financial":
        return {"status": "FAIL", "detail": "Decimal money scaffolding pending"}

    if gate_id == "G12_deployment":
        status = "PASS" if probes_py.is_file() and "readiness_checks" in probes_py.read_text(encoding="utf-8") else "FAIL"
        return {
            "status": status,
            "detail": "readiness/version probes in Factory kit" if status == "PASS" else "probes missing",
        }

    if gate_id == "G13_oracle":
        status = "NOT VERIFIED" if not oracle_script.is_file() else "FAIL"
        return {
            "status": status,
            "detail": "oracle v2 skeleton only — suites not implemented",
        }

    if gate_id == "G14_documentation":
        audit = ROOT / "docs" / "audits" / "STEWARD_V2_AGENT_AUDIT.md"
        status = "PASS" if audit.is_file() and gate_script.is_file() else "FAIL"
        return {
            "status": status,
            "detail": "v2 audit + gate script present" if status == "PASS" else "documentation incomplete",
        }

    return {"status": "NOT VERIFIED", "detail": "unknown gate"}


def run_gates() -> Dict[str, Any]:
    results = []
    for gate in GATES:
        gate_id = gate["gate"]
        verdict = _evaluate_gate(gate_id)
        results.append({**gate, **verdict})

    mandatory_fail = any(r["status"] == "FAIL" for r in results)
    mandatory_not_verified = any(r["status"] == "NOT VERIFIED" for r in results)
    pilot_verdict = "NO-GO" if mandatory_fail or mandatory_not_verified else "GO"

    return {
        "schema_version": "steward_gate_v2",
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(ROOT),
        "pilot_verdict": pilot_verdict,
        "gates": results,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Steward pilot certification v2 gate runner")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "steward_gate_v2.json",
        help="Write JSON report to this path",
    )
    parser.add_argument("--print", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(argv)

    report = run_gates()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.print:
        print(payload, end="")
    print(f"Gate report written to {args.out}", file=sys.stderr)
    print(f"Pilot verdict: {report['pilot_verdict']}", file=sys.stderr)
    return 0 if report["pilot_verdict"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
