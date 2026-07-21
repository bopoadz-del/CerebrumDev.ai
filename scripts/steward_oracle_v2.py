#!/usr/bin/env python3
"""Steward Oracle V2 skeleton runner.

Suites A–R are declared but not fully implemented in this tranche.
Use steward_gate_v2.py for mandatory gate status until suites land.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]

SUITES: List[Dict[str, str]] = [
    {"suite": "A", "name": "health_and_version", "mandatory": "true"},
    {"suite": "B", "name": "readiness_fail_closed", "mandatory": "true"},
    {"suite": "C", "name": "authentication_anonymous_denied", "mandatory": "true"},
    {"suite": "D", "name": "authorization_cross_estate_denied", "mandatory": "true"},
    {"suite": "E", "name": "canonical_rag_query", "mandatory": "true"},
    {"suite": "F", "name": "legacy_rag_disabled", "mandatory": "true"},
    {"suite": "G", "name": "ingest_authz", "mandatory": "true"},
    {"suite": "H", "name": "admin_routes_disabled", "mandatory": "true"},
    {"suite": "I", "name": "insufficiency_policy", "mandatory": "true"},
    {"suite": "J", "name": "workflow_oracles", "mandatory": "true"},
    {"suite": "K", "name": "agent_runtime", "mandatory": "true"},
    {"suite": "L", "name": "resident_engineer_security", "mandatory": "true"},
    {"suite": "M", "name": "financial_integrity", "mandatory": "true"},
    {"suite": "N", "name": "store_execution", "mandatory": "true"},
    {"suite": "O", "name": "provenance_and_version_pin", "mandatory": "true"},
    {"suite": "P", "name": "determinism", "mandatory": "true"},
    {"suite": "Q", "name": "documentation_honesty", "mandatory": "true"},
    {"suite": "R", "name": "deployment_readiness_live", "mandatory": "true"},
]


def _http_get(base_url: str, path: str, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    req = Request(url, headers=headers or {}, method="GET")
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return {
                "ok": True,
                "status": resp.status,
                "body": json.loads(body) if body else {},
            }
    except URLError as exc:
        return {"ok": False, "status": None, "error": str(exc.reason)}


def run_oracle(base_url: str | None) -> Dict[str, Any]:
    suites: List[Dict[str, Any]] = []
    for spec in SUITES:
        entry: Dict[str, Any] = {**spec, "status": "NOT VERIFIED", "detail": "suite not implemented"}
        if base_url and spec["suite"] == "A":
            health = _http_get(base_url, "/health")
            version = _http_get(base_url, "/version")
            ready = _http_get(base_url, "/ready")
            passed = (
                health.get("status") == 200
                and version.get("status") == 200
                and ready.get("status") in {200, 503}
            )
            entry["status"] = "PASS" if passed else "FAIL"
            entry["detail"] = {
                "health": health.get("status"),
                "version": version.get("status"),
                "ready": ready.get("status"),
            }
        suites.append(entry)

    mandatory_fail = any(s["mandatory"] == "true" and s["status"] == "FAIL" for s in suites)
    mandatory_nv = any(s["mandatory"] == "true" and s["status"] == "NOT VERIFIED" for s in suites)
    verdict = "NO-GO" if mandatory_fail or mandatory_nv else "GO"

    return {
        "schema_version": "steward_oracle_v2",
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "verdict": verdict,
        "suites": suites,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Steward Oracle V2 skeleton runner")
    parser.add_argument("--base-url", default=None, help="Live Steward base URL (optional)")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "steward_oracle_v2.json",
        help="Write JSON report to this path",
    )
    parser.add_argument("--print", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(argv)

    report = run_oracle(args.base_url)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.print:
        print(payload, end="")
    print(f"Oracle report written to {args.out}", file=sys.stderr)
    print(f"Verdict: {report['verdict']}", file=sys.stderr)
    return 0 if report["verdict"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
