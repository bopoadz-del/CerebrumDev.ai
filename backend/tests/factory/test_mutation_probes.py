"""The mutation probe suite must pass and must not need network."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.factory.build.mutation_probes import PROBES, main


def test_probe_roster_covers_the_zero_artifact_hole():
    names = {p.__name__ for p in PROBES}
    assert "probe_zero_agent_artifacts_refuses_writer_gate" in names
    assert "probe_empty_blueprint_refuses_handoff" in names
    assert "probe_unmeasured_authorship_is_below_floor" in names
    assert "probe_whole_chain_goes_red" in names


def test_mutation_probe_suite_passes_in_process():
    assert main() == 0


def test_mutation_probe_suite_passes_as_a_module():
    root = Path(__file__).resolve().parents[2]  # backend/
    result = subprocess.run(
        [sys.executable, "-m", "app.factory.build.mutation_probes"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL" in result.stdout
