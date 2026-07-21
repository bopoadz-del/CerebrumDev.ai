"""Wave 2: steward_gate_v2 enhanced checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_gate_runner_reports_wave2_passes():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "steward_gate_v2.py"), "--print"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 1  # NO-GO expected — mandatory FAIL/NOT VERIFIED remain
    report = json.loads(proc.stdout)
    by_gate = {g["gate"]: g for g in report["gates"]}

    assert by_gate["G2_authentication"]["status"] == "PASS"
    assert by_gate["G5_database"]["status"] == "PASS"
    assert by_gate["G10_resident_engineer"]["status"] == "PASS"
    assert by_gate["G11_financial"]["status"] == "PASS"
    assert by_gate["G12_deployment"]["status"] == "PASS"
    assert by_gate["G14_documentation"]["status"] == "PASS"

    assert by_gate["G7_store"]["status"] == "FAIL"
    assert by_gate["G13_oracle"]["status"] == "FAIL"


def test_oracle_static_suites_score_expected():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "steward_oracle_v2.py"), "--print"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    report = json.loads(proc.stdout)
    by_suite = {s["suite"]: s for s in report["suites"]}
    assert by_suite["F"]["status"] == "PASS"
    assert by_suite["H"]["status"] == "PASS"
    assert by_suite["L"]["status"] == "PASS"
    assert by_suite["M"]["status"] == "PASS"
    assert by_suite["N"]["status"] == "FAIL"
