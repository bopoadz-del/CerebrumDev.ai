"""The sharded backend CI must run every collected test exactly once.

scripts/ci_test_shards.py plans which test FILES each runner takes and, after
the run, proves from the shards' own JUnit reports that nothing was dropped or
run twice. These tests pin both halves: the plan is an exact, deterministic
partition, and the proof goes red on a dropped file, a duplicated file and a
file whose shard reported fewer cases than were collected.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ci_test_shards", ROOT / "scripts" / "ci_test_shards.py")
shards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shards)

IDS = [
    *(f"tests/test_a.py::t{i}" for i in range(5)),
    *(f"tests/factory/test_b.py::T::t{i}[p]" for i in range(3)),
    "tests/test_c.py::t0",
    *(f"tests/test_d.py::t{i}" for i in range(7)),
]
FILES = {"tests/test_a.py": 5, "tests/factory/test_b.py": 3, "tests/test_c.py": 1, "tests/test_d.py": 7}


def test_the_plan_is_an_exact_partition_of_the_collected_files():
    for total in (1, 2, 3, 8):
        planned = shards.plan(IDS, total, {})
        flat = [f for shard in planned for f in shard]
        assert sorted(flat) == sorted(FILES), total
        assert len(flat) == len(set(flat)), total


def test_every_runner_derives_the_same_plan():
    durations = {"tests/test_d.py": 30.0, "tests/test_a.py": 2.0}
    assert shards.plan(IDS, 3, durations) == shards.plan(list(IDS), 3, dict(durations))


def test_measured_seconds_drive_the_packing():
    # One heavy file must sit alone; the light ones share the other shard.
    measured = {"tests/test_c.py": 600.0, "tests/test_a.py": 1.0, "tests/factory/test_b.py": 1.0, "tests/test_d.py": 1.0}
    planned = shards.plan(IDS, 2, measured)
    assert ["tests/test_c.py"] in planned


def _junit(path: Path, per_file: dict[str, int]) -> None:
    cases = "".join(
        f'<testcase file="{f}" classname="x" name="t{i}" time="0.1"/>'
        for f, n in per_file.items()
        for i in range(n)
    )
    path.write_text(f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>', encoding="utf-8")


def _verify(tmp_path: Path, reports: list[dict[str, int]]) -> int:
    ids = tmp_path / "ids.txt"
    ids.write_text("\n".join(IDS) + "\n", encoding="utf-8")
    junit = tmp_path / "junit"
    junit.mkdir()
    for i, per_file in enumerate(reports):
        _junit(junit / f"shard-{i}.xml", per_file)
    return shards.main(["verify", "--ids", str(ids), "--junit", str(junit), "--total", str(len(reports))])


def test_full_coverage_exactly_once_passes(tmp_path):
    assert _verify(tmp_path, [
        {"tests/test_a.py": 5, "tests/test_c.py": 1},
        {"tests/factory/test_b.py": 3, "tests/test_d.py": 7},
    ]) == 0


def test_a_dropped_file_fails(tmp_path):
    assert _verify(tmp_path, [
        {"tests/test_a.py": 5, "tests/test_c.py": 1},
        {"tests/test_d.py": 7},
    ]) == 1


def test_a_file_run_on_two_shards_fails(tmp_path):
    assert _verify(tmp_path, [
        {"tests/test_a.py": 5, "tests/test_c.py": 1, "tests/test_d.py": 7},
        {"tests/factory/test_b.py": 3, "tests/test_d.py": 7},
    ]) == 1


def test_a_short_count_fails(tmp_path):
    assert _verify(tmp_path, [
        {"tests/test_a.py": 4, "tests/test_c.py": 1},
        {"tests/factory/test_b.py": 3, "tests/test_d.py": 7},
    ]) == 1
