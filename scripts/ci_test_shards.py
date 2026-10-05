#!/usr/bin/env python3
"""Split the backend pytest suite across parallel CI runners, and prove it.

The serial suite outgrew its job (21.7 min for 3,794 tests by 2026-10-05).
The fix is wall time, not a longer cap: the suite is split BY FILE across a
matrix of runners. A file never spans two runners, so every file still runs
its tests in one process, in order, exactly as it did serially -- no test
has to become parallel-safe for this to hold.

Subcommands (stdlib only; ``plan`` and ``collect`` run inside backend/):

  collect  --out ids.txt
      ``pytest --collect-only -q`` -> one node id per line.
  plan     --ids ids.txt --total N --index I [--durations F] --out files.txt
      Longest-processing-time packing of files into N shards by measured
      seconds per file (``F``); a file with no measurement is weighted by its
      test count times the measured median seconds per test. Deterministic:
      every shard computes the same plan from the same collection.
  verify   --ids ids.txt --junit DIR [--total N]
      Reads every shard's JUnit report and fails unless each collected test
      file appears in exactly one shard and that shard reported exactly as
      many test cases for it as the full collection holds. No test dropped,
      none run twice.
  durations --junit DIR --out F
      Seconds per file from the shards' JUnit reports, to re-balance later.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path


def _file_of(node_id: str) -> str:
    return node_id.split("::", 1)[0].replace("\\", "/")


def _read_ids(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if "::" in ln]


def cmd_collect(args: argparse.Namespace) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
    )
    ids = [ln.strip() for ln in proc.stdout.splitlines() if "::" in ln]
    if proc.returncode != 0 or not ids:
        sys.stdout.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        print(f"collection failed (rc={proc.returncode}, {len(ids)} ids)", file=sys.stderr)
        return 1
    Path(args.out).write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"collected {len(ids)} tests in {len({_file_of(i) for i in ids})} files")
    return 0


def plan(ids: list[str], total: int, durations: dict[str, float]) -> list[list[str]]:
    counts = Counter(_file_of(i) for i in ids)
    per_test = sorted(
        durations[f] / counts[f] for f in counts if f in durations and counts[f]
    )
    median = per_test[len(per_test) // 2] if per_test else 1.0
    weight = {f: durations.get(f, counts[f] * median) for f in counts}
    shards: list[list[str]] = [[] for _ in range(total)]
    load = [0.0] * total
    # Heaviest first onto the lightest shard; ties broken by name so every
    # runner derives the identical plan.
    for f in sorted(weight, key=lambda f: (-weight[f], f)):
        i = min(range(total), key=lambda k: (load[k], k))
        shards[i].append(f)
        load[i] += weight[f]
    for i in range(total):
        print(f"shard {i}: {len(shards[i])} files, ~{load[i]:.0f}s", file=sys.stderr)
    return [sorted(s) for s in shards]


def cmd_plan(args: argparse.Namespace) -> int:
    ids = _read_ids(Path(args.ids))
    durations: dict[str, float] = {}
    if args.durations and Path(args.durations).is_file():
        durations = json.loads(Path(args.durations).read_text(encoding="utf-8"))
    files = plan(ids, args.total, durations)[args.index]
    Path(args.out).write_text("\n".join(files) + "\n", encoding="utf-8")
    print(f"shard {args.index}/{args.total}: {len(files)} files")
    return 0


def _junit_cases(junit_dir: Path) -> dict[str, dict[str, int]]:
    """{report name: {file: test cases}} from xunit1 reports (``file`` attr)."""
    out: dict[str, dict[str, int]] = {}
    for report in sorted(junit_dir.rglob("*.xml")):
        per_file: Counter = Counter()
        for case in ET.parse(report).getroot().iter("testcase"):
            f = (case.get("file") or "").replace("\\", "/")
            if f:
                per_file[f] += 1
        out[str(report.relative_to(junit_dir))] = dict(per_file)
    return out


def cmd_verify(args: argparse.Namespace) -> int:
    expected = Counter(_file_of(i) for i in _read_ids(Path(args.ids)))
    reports = _junit_cases(Path(args.junit))
    if args.total and len(reports) != args.total:
        print(f"FAIL: {len(reports)} shard reports, expected {args.total}")
        return 1
    seen: dict[str, list[str]] = defaultdict(list)
    ran: Counter = Counter()
    for name, per_file in reports.items():
        for f, n in per_file.items():
            seen[f].append(name)
            ran[f] += n
    problems = []
    for f in sorted(set(expected) | set(ran)):
        if len(seen.get(f, [])) > 1:
            problems.append(f"{f}: ran in {len(seen[f])} shards {seen[f]}")
        if ran.get(f, 0) != expected.get(f, 0):
            problems.append(f"{f}: collected {expected.get(f, 0)}, ran {ran.get(f, 0)}")
    total_expected, total_ran = sum(expected.values()), sum(ran.values())
    for name, per_file in reports.items():
        print(f"  {name}: {sum(per_file.values())} tests in {len(per_file)} files")
    print(f"collected {total_expected} tests in {len(expected)} files; "
          f"shards ran {total_ran} tests in {len(ran)} files")
    if problems:
        print("FAIL: the shards do not cover the suite exactly once:")
        for p in problems[:50]:
            print("  " + p)
        return 1
    print("OK: every collected test ran exactly once across the shards")
    return 0


def cmd_durations(args: argparse.Namespace) -> int:
    secs: dict[str, float] = defaultdict(float)
    for report in Path(args.junit).rglob("*.xml"):
        for case in ET.parse(report).getroot().iter("testcase"):
            f = (case.get("file") or "").replace("\\", "/")
            if f:
                secs[f] += float(case.get("time") or 0.0)
    Path(args.out).write_text(
        json.dumps({f: round(s, 2) for f, s in sorted(secs.items())}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"{len(secs)} files, {sum(secs.values()):.0f}s total")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_collect)
    pl = sub.add_parser("plan")
    pl.add_argument("--ids", required=True)
    pl.add_argument("--total", type=int, required=True)
    pl.add_argument("--index", type=int, required=True)
    pl.add_argument("--durations")
    pl.add_argument("--out", required=True)
    pl.set_defaults(fn=cmd_plan)
    v = sub.add_parser("verify")
    v.add_argument("--ids", required=True)
    v.add_argument("--junit", required=True)
    v.add_argument("--total", type=int, default=0)
    v.set_defaults(fn=cmd_verify)
    d = sub.add_parser("durations")
    d.add_argument("--junit", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_durations)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
