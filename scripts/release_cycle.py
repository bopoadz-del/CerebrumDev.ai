#!/usr/bin/env python3
"""The release cycle: after a deploy, smoke A and the repro builds run AT THE
SAME TIME, each on its own verified account; smoke B runs once all of them are
done; one report says what shipped.

Owner's standing rules (2026-10-08): repro builds run in parallel with smoke A
on separate accounts; the smoke keeps its reserved build slot; slots queue,
never fail. The whole cycle runs on GitHub Actions (post-deploy-smoke.yml),
never as a local background process -- a laptop's memory must never decide
whether a cycle finishes. On 2026-10-07 every cycle ran the second repro AFTER
smoke A, by hand, from a local watcher that kept expiring.

One commit per cycle. ``live-sha`` resolves the commit the cycle is about
(the deployed sha, or whatever /version serves now) and fails closed unless
live /version IS that commit; the report re-reads /version and fails the cycle
if it moved.

Accounts. The smoke gate issues a roster of verified principals
(``/v1/auth/smoke-login`` with ``principals``). Index 0 is the smoke's own --
the only account whose builds may use the worker's reserved slot. The repros
take indices 1..K, one each. Fewer accounts than runs FAILS CLOSED: two builds
on one account share its tenant slot and would run one after the other while
the report claimed a parallel cycle.

Briefs are data (``scripts/release_cycle.json``); this file knows none of them.

What a cycle builds (owner ruling, 2026-10-08). The ANCHORS -- the regression
pair in ``release_cycle.json``, whose certified exports make a flip there a
regression -- plus ROTATION picks from the pool in ``backend/tests/repro_pool``
(at least eight blueprints across verticals, each with its own country,
currency and build level). Cycle ``k`` builds ``pool[(p*k + i) mod n]`` for the
pool's ``p`` picks, so with eight blueprints and two picks each comes round
every four cycles. Nothing here knows any blueprint; nothing is random. ``k``
is git history: the number of first-parent commits from the one that added the
pool index to the cycle's commit -- every master push deploys and starts a
cycle, a re-run on the same commit picks the same pair, and no counter is
stored anywhere. Release = every anchor AND every pick exports certified, and
smoke A and smoke B pass, on one commit.

    # the commit this cycle is about (prints sha=...; exit 1 unless it is live)
    python scripts/release_cycle.py live-sha [--expect <sha>]
    # what a cycle on this commit builds (anchors + rotation picks), offline
    python scripts/release_cycle.py plan
    # the repros (anchors + this cycle's picks), beside the live-smoke job
    python scripts/release_cycle.py repros --out cycle/
    # after smoke A and the repros: smoke B ran; merge one report
    python scripts/release_cycle.py report --out cycle/ --smoke-a pass --smoke-b pass
    # everything in one process, for a developer reproducing CI
    python scripts/release_cycle.py full --out cycle/
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "post_deploy_smoke.py"
POOL_SCRIPT = ROOT / "scripts" / "repro_pool.py"
DEFAULT_CONFIG = ROOT / "scripts" / "release_cycle.json"
#: Run roles in the report: the regression pair, and the cycle's pool picks.
ANCHOR = "anchor"
ROTATION = "rotation"
#: The typed Floor intake a pool blueprint declares (the chat request's
#: ``vertical`` / ``country`` / ``currency`` fields).
INTAKE_FIELDS = ("vertical", "country", "currency")

#: The smoke's own principal on the roster. Only its builds may use the
#: worker's reserved slot (server: trial_limits.SMOKE_RESERVED_PRINCIPAL).
SMOKE_ACCOUNT_INDEX = 0
#: Seconds between polls of a running build.
POLL_S = 30
#: Seconds between progress lines per run.
PROGRESS_S = 300
#: The report file inside --out.
REPORT_NAME = "cycle_report.json"
REPROS_NAME = "repros.json"
#: The fields of an export's MANIFEST.json the report carries.
MANIFEST_FIELDS = ("certified", "build_level", "advisory_checks")


class CycleError(RuntimeError):
    """The cycle cannot run as planned (config, accounts)."""


# -- config ------------------------------------------------------------------


def load_config(path: Path | str = DEFAULT_CONFIG) -> Dict[str, Any]:
    """The cycle's data: build level, the anchor briefs, the rotation pool and
    how many builds the live box runs at once."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    anchors = data.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise CycleError(f"{path}: 'anchors' must be a non-empty list")
    for entry in anchors:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            raise CycleError(f"{path}: every anchor needs a 'name'")
        if not str(entry.get("brief") or "").strip():
            raise CycleError(f"{path}: anchor {entry.get('name')!r} has no 'brief'")
    names = [str(r["name"]) for r in anchors]
    if len(names) != len(set(names)):
        raise CycleError(f"{path}: anchor names must be distinct: {names}")
    rotation = data.get("rotation")
    if not isinstance(rotation, dict) or not str(rotation.get("pool") or "").strip():
        raise CycleError(f"{path}: 'rotation' must name its 'pool' index")
    slots = data.get("user_build_slots")
    if not isinstance(slots, int) or isinstance(slots, bool) or slots < 1:
        raise CycleError(f"{path}: 'user_build_slots' must be a positive integer")
    data.setdefault("build_level", "production")
    return data


def _pool_module():
    spec = importlib.util.spec_from_file_location("repro_pool", POOL_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_rotation(config: Mapping[str, Any]) -> Dict[str, Any]:
    """The configured pool in its declared order, and how many blueprints a
    cycle picks from it -- both from the pool index, the one source
    (scripts/repro_pool.py)."""
    mod = _pool_module()
    index = ROOT / str(config["rotation"]["pool"])
    try:
        return {"pool": mod.load_pool(index), "picks": int(mod.load_index(index)["picks_per_cycle"])}
    except mod.PoolError as exc:
        raise CycleError(str(exc)) from exc


# -- the rotation ------------------------------------------------------------


def select_rotation(pool: list, *, k: int, picks: int) -> list:
    """Cycle ``k``'s picks: ``pool[(picks*k + i) mod n]`` for i in 0..picks-1.

    Deterministic -- the pool's declared order and the counter decide it, no
    clock and no random source -- and a cycle never builds one blueprint twice.
    """
    n = len(pool)
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise CycleError(f"the rotation counter must be a non-negative integer, got {k!r}")
    if not 1 <= picks <= n:
        raise CycleError(f"a cycle picks 1..{n} blueprints from a pool of {n}, not {picks}")
    return [pool[(picks * k + i) % n] for i in range(picks)]


def _git(repo_root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise CycleError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()


def rotation_index(sha: str, *, repo_root: Path | str = ROOT, index_rel: str) -> int:
    """The cycle counter ``k`` for ``sha``: how many first-parent commits lie
    between the commit that ADDED the pool index and ``sha`` (0 on that commit).

    Git history is the record -- auditable, identical on every runner, nothing
    stored. Fails closed on a shallow checkout (the history is not there to
    count) and on a commit from before the pool existed; it never guesses.
    """
    repo = Path(repo_root)
    if _git(repo, "rev-parse", "--is-shallow-repository") == "true":
        raise CycleError(
            "the rotation counter needs full git history; this checkout is shallow "
            "(actions/checkout fetch-depth: 0)"
        )
    added = _git(repo, "log", "--diff-filter=A", "--format=%H", sha, "--", index_rel).splitlines()
    if not added:
        raise CycleError(f"{index_rel} does not exist at {sha}: there is no rotation to count")
    landed = added[-1]
    return int(_git(repo, "rev-list", "--count", "--first-parent", f"{landed}..{sha}"))


def plan_runs(config: Mapping[str, Any], pool: list, *, k: int, picks: int) -> list:
    """Every build this cycle runs, in order: the anchors, then cycle ``k``'s
    rotation picks. Each carries its brief, level and typed intake (anchors
    declare none, exactly as they always have)."""
    runs = [
        {"name": str(a["name"]), "role": ANCHOR, "brief": a["brief"],
         "level": config["build_level"], "intake": {}}
        for a in config["anchors"]
    ]
    for bp in select_rotation(pool, k=k, picks=picks):
        runs.append({
            "name": str(bp["id"]), "role": ROTATION, "brief": bp["brief"],
            "level": bp["build_level"],
            "intake": {field: bp[field] for field in INTAKE_FIELDS},
        })
    names = [r["name"] for r in runs]
    if len(names) != len(set(names)):
        raise CycleError(f"run names must be distinct: {names}")
    return runs


def repro_wait_s(*, builds: int, user_slots: int, build_wait_s: float) -> float:
    """How long one repro may take: a build's own ceiling times the waves the
    live box needs to run them all. Slots QUEUE, so a repro in the second wave
    is waiting, not failing -- it must not be timed out while it waits."""
    waves = max(1, math.ceil(int(builds) / max(1, int(user_slots))))
    return float(build_wait_s) * waves


# -- the live commit ---------------------------------------------------------


def read_live_sha(smoke: Any) -> Optional[str]:
    """The full git sha live ``/version`` serves, or None when it answers
    nothing usable (down, restarting, no sha)."""
    status, body = smoke.req("GET", "/version")
    if status != 200 or not isinstance(body, dict):
        return None
    sha = str(body.get("git_sha") or "").strip()
    return sha or None


def wait_for_live_sha(smoke: Any, expect: str, *, wait_s: float, poll_s: float = 15) -> str:
    """Wait until live /version serves exactly ``expect``; fail closed if it
    never does. Rides out a restart (no answer) and a rollout still serving
    the previous commit. Exact match only: a cycle reports ONE commit."""
    expect = str(expect).strip()
    deadline = time.monotonic() + max(0.0, float(wait_s))
    seen: Optional[str] = None
    while True:
        seen = read_live_sha(smoke)
        if seen == expect:
            return seen
        if time.monotonic() >= deadline:
            raise CycleError(
                f"live /version serves {seen or 'nothing'}, not {expect}; "
                "refusing to run a cycle that would report a commit it did not run on"
            )
        time.sleep(poll_s)


# -- accounts ----------------------------------------------------------------


def assign_accounts(names: Iterable[str], *, available: int) -> Dict[str, Any]:
    """The smoke keeps index 0; each repro gets the next index of its own.

    Fails closed when the roster is short -- never two runs on one account.
    """
    names = list(names)
    if len(names) != len(set(names)):
        raise CycleError(f"repro names must be distinct: {names}")
    needed = len(names) + 1
    if available < needed:
        raise CycleError(
            f"the cycle needs {needed} verified accounts (smoke + {len(names)} "
            f"repros, one each) and the roster has {available}; refusing to "
            "put two builds on one account"
        )
    return {
        "smoke": SMOKE_ACCOUNT_INDEX,
        "repros": {name: SMOKE_ACCOUNT_INDEX + 1 + i for i, name in enumerate(names)},
    }


# -- running things side by side ---------------------------------------------


def run_parallel(tasks: Mapping[str, Callable[[], Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Start every task at once; collect each result. A task that raises is
    recorded as ``{"status": "error"}`` and never cancels the others."""
    results: Dict[str, Dict[str, Any]] = {}
    lock = threading.Lock()

    def one(name: str, fn: Callable[[], Dict[str, Any]]) -> None:
        started = time.monotonic()
        try:
            out = dict(fn() or {})
        except Exception as exc:  # noqa: BLE001 -- one run's failure is its own row
            out = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        out.setdefault("wall_s", round(time.monotonic() - started, 1))
        with lock:
            results[name] = out

    if not tasks:
        return results
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        for fut in [pool.submit(one, n, fn) for n, fn in tasks.items()]:
            fut.result()
    return results


# -- one build, the way a customer drives it ----------------------------------


def summarize_export(blob: bytes) -> Dict[str, Any]:
    """Size, file count and the certification fields of the zip's MANIFEST."""
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    manifest: Dict[str, Any] = {}
    found = [n for n in names if n.rsplit("/", 1)[-1] == "MANIFEST.json"]
    if found:
        raw = json.loads(zf.read(sorted(found, key=len)[0]))
        manifest = {k: raw.get(k) for k in MANIFEST_FIELDS if k in raw}
    return {"bytes": len(blob), "files": len(names), "manifest": manifest}


def _detail(blob: Any) -> str:
    if isinstance(blob, (bytes, bytearray)):
        try:
            blob = json.loads(blob)
        except Exception:  # noqa: BLE001 -- not JSON: show the text
            return blob[:300].decode(errors="replace")
    if isinstance(blob, dict):
        return str(blob.get("detail") or blob)[:500]
    return str(blob)[:500]


def drive_build(
    smoke: Any,
    token: str,
    brief: str,
    *,
    level: str,
    wait_s: float,
    poll_s: float = POLL_S,
    label: str = "",
    save_to: Optional[Path] = None,
    intake: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Draft, choose the level, confirm, approve, then wait for the build's own
    terminal state and take the export. Uses the smoke's client so the flow is
    exactly the smoke's. A transient gateway answer while polling (a platform
    restart) is a reason to keep polling -- never the build's verdict.

    ``intake`` is the typed country / currency / vertical a customer enters on
    the intake line. It rides with the level choice, and again with Approve, so
    a proposal the chat model made from the brief and Confirm stored in between
    can never replace what was typed."""
    started = time.monotonic()
    s, body = smoke.req("POST", "/v1/sessions/", {}, token=token)
    sid = (body or {}).get("session_id") if isinstance(body, dict) else None
    if not sid:
        return {"status": "error", "error": f"session create http={s}"}
    typed = {k: v for k, v in (intake or {}).items() if k in INTAKE_FIELDS and v}
    extra = {"fields": typed} if typed else {}
    smoke.chat_until_drafted(sid, token, brief)
    smoke.chat(sid, token, "", action="set_build_level", value=level, **extra)
    smoke.chat(sid, token, "", action="confirm_intake")
    smoke.chat(sid, token, "", action="approve", **extra)

    transient = getattr(smoke, "TRANSIENT", {502, 503, 504})
    # The build's own declared deadline (build-status ``deadline``); wait_s
    # only for a server that declares none.
    deadline = smoke.BuildDeadline(wait_s, time.monotonic)
    last_print = 0.0
    http, blob, build = 0, b"", {}
    while True:
        http, blob = smoke.req("GET", f"/v1/sessions/{sid}/product/package", token=token, raw=True)
        _st, status = smoke.req("GET", f"/v1/sessions/{sid}/product/build-status", token=token)
        status = status if isinstance(status, dict) else {}
        build = status.get("build") if isinstance(status.get("build"), dict) else status
        deadline.read(build)
        state = build.get("state")
        if http == 200:
            break
        if http not in transient and http != 409:
            break
        if http == 409 and state in {"failed", "stalled"}:
            break
        if deadline.passed():
            break
        now = time.monotonic()
        if label and now - last_print >= PROGRESS_S:
            print(f"[{label}] waiting http={http} {state} "
                  f"{build.get('phases_done')}/{build.get('phases_total')}", flush=True)
            last_print = now
        time.sleep(poll_s)

    out: Dict[str, Any] = {
        "session_id": sid,
        "package_http": http,
        "build_state": build.get("state"),
        "wall_s": round(time.monotonic() - started, 1),
    }
    if http == 200 and isinstance(blob, (bytes, bytearray)) and blob[:2] == b"PK":
        out["status"] = "exported"
        out["export"] = summarize_export(bytes(blob))
        if save_to is not None:
            save_to.mkdir(parents=True, exist_ok=True)
            zpath = save_to / f"export_{label or 'run'}_{sid}.zip"
            zpath.write_bytes(bytes(blob))
            out["export"]["zip"] = str(zpath)
            manifest = zipfile.ZipFile(io.BytesIO(bytes(blob)))
            names = [n for n in manifest.namelist() if n.endswith("MANIFEST.json")]
            if names:
                (save_to / f"MANIFEST_{label or 'run'}.json").write_bytes(
                    manifest.read(sorted(names, key=len)[0])
                )
        return out
    out["status"] = "failed" if build.get("state") in {"failed", "stalled"} else "incomplete"
    out["detail"] = _detail(blob)
    return out


# -- the report --------------------------------------------------------------


def _repro_passed(run: Mapping[str, Any]) -> bool:
    export = run.get("export") or {}
    return run.get("status") == "exported" and (export.get("manifest") or {}).get("certified") is True


def build_report(
    *,
    smoke_a: Mapping[str, Any],
    repros: Mapping[str, Mapping[str, Any]],
    smoke_b: Mapping[str, Any],
    started_at: float,
    finished_at: float,
    commit: str,
    live_sha: Optional[str],
    rotation: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """One record of the cycle. Passes only when both smokes pass, every
    repro -- each anchor AND each rotation pick -- exported a CERTIFIED zip,
    and live /version still serves the commit the report names (``live_sha``,
    read when the report is written). The cycle's wall time is recorded, never
    judged. ``rotation`` names which counter value and picks this cycle ran."""
    cycle_s = round(float(finished_at) - float(started_at), 1)
    reasons = []
    if smoke_a.get("status") != "pass":
        reasons.append("smoke A failed")
    if smoke_b.get("status") != "pass":
        reasons.append("smoke B failed")
    if not repros:
        reasons.append("no repro ran")
    reasons += [f"{name} did not export certified" for name, r in repros.items() if not _repro_passed(r)]
    if not commit or live_sha != commit:
        reasons.append(f"live /version serves {live_sha or 'nothing'}, not the cycle's commit {commit or '?'}")
    runs: Dict[str, Any] = {"smoke_a": dict(smoke_a)}
    runs.update({name: dict(r) for name, r in repros.items()})
    runs["smoke_b"] = dict(smoke_b)
    report: Dict[str, Any] = {
        "commit": commit,
        "live_sha": live_sha,
        "verdict": "fail" if reasons else "pass",
        "reason": "; ".join(reasons),
        "cycle_s": cycle_s,
        "runs": runs,
    }
    if rotation is not None:
        report["rotation"] = dict(rotation)
    return report


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"## Release cycle on `{str(report.get('commit'))[:8]}`: {report['verdict'].upper()}",
        "",
        f"Cycle time {report['cycle_s'] / 60:.0f} min.",
        "",
    ]
    rotation = report.get("rotation") or {}
    if rotation:
        lines += [
            f"Rotation k={rotation.get('k')} of a pool of {rotation.get('pool_size')}: "
            + ", ".join(str(p) for p in rotation.get("picks") or []),
            "",
        ]
    if report.get("reason"):
        lines += [f"Why: {report['reason']}", ""]
    lines += [
        "| Run | Role | Account | Result | Export | Certified | Build level | Advisory | Wall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, run in report["runs"].items():
        export = run.get("export") or {}
        manifest = export.get("manifest") or {}
        size = f"{export['bytes']:,} B / {export['files']} files" if export else "—"
        wall = f"{float(run['wall_s']) / 60:.0f} min" if run.get("wall_s") is not None else "—"
        role = run.get("role") or ("smoke" if name.startswith("smoke") else "—")
        lines.append(
            f"| {name} | {role} | {run.get('account_index', '—')} | {run.get('status')} | {size} | "
            f"{_cell(manifest.get('certified')) if export else '—'} | "
            f"{_cell(manifest.get('build_level')) if export else '—'} | "
            f"{_cell(manifest.get('advisory_checks')) if export else '—'} | {wall} |"
        )
    return "\n".join(lines) + "\n"


# -- CLI ---------------------------------------------------------------------


def _load_smoke(base: str):
    spec = importlib.util.spec_from_file_location("post_deploy_smoke", SMOKE_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = mod.resolve_base(["release_cycle", base])
    return mod


def plan_cycle(config: Mapping[str, Any], commit: str) -> Dict[str, Any]:
    """This cycle's builds and the rotation record the report carries."""
    rot = load_rotation(config)
    sha = commit or _git(ROOT, "rev-parse", "HEAD")
    k = rotation_index(sha, repo_root=ROOT, index_rel=str(config["rotation"]["pool"]))
    runs = plan_runs(config, rot["pool"], k=k, picks=rot["picks"])
    rotation = {
        "k": k,
        "picks": [r["name"] for r in runs if r["role"] == ROTATION],
        "pool_size": len(rot["pool"]),
        "counter": f"first-parent commits since {config['rotation']['pool']} was added, at {sha[:12]}",
    }
    return {"runs": runs, "rotation": rotation}


def _repro_tasks(
    smoke: Any, config: Mapping[str, Any], runs: list, out: Path,
) -> Dict[str, Callable[[], Dict[str, Any]]]:
    names = [r["name"] for r in runs]
    roster = smoke.verified_token_roster(len(names) + 1)
    plan = assign_accounts(names, available=len(roster))
    wait_s = repro_wait_s(
        builds=len(runs),
        user_slots=int(config["user_build_slots"]),
        build_wait_s=float(getattr(smoke, "BUILD_WAIT_S", 5400)),
    )
    tasks: Dict[str, Callable[[], Dict[str, Any]]] = {}
    for run in runs:
        index = plan["repros"][run["name"]]

        def task(run=run, index=index):
            result = drive_build(
                smoke, roster[index], run["brief"],
                level=run["level"], wait_s=wait_s, label=run["name"], save_to=out,
                intake=run["intake"],
            )
            result["account_index"] = index
            result["role"] = run["role"]
            return result

        tasks[run["name"]] = task
    return tasks


def _smoke_subprocess(base: str) -> Dict[str, Any]:
    started = time.monotonic()
    code = subprocess.call([sys.executable, str(SMOKE_SCRIPT), base])
    return {
        "status": "pass" if code == 0 else "fail",
        "exit_code": code,
        "account_index": SMOKE_ACCOUNT_INDEX,
        "wall_s": round(time.monotonic() - started, 1),
    }


def _write(out: Path, name: str, data: Any) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return path


def _finish(out: Path, report: Dict[str, Any]) -> int:
    _write(out, REPORT_NAME, report)
    md = render_markdown(report)
    (out / "cycle_report.md").write_text(md, encoding="utf-8")
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md)
    return 0 if report["verdict"] == "pass" else 1


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=("live-sha", "plan", "repros", "report", "full"))
    parser.add_argument("--base", default=os.environ.get("FACTORY_BASE_URL") or "https://api.cerebrum-dev.com")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out", default="cycle")
    parser.add_argument("--smoke-a", choices=("pass", "fail"))
    parser.add_argument("--smoke-b", choices=("pass", "fail"))
    parser.add_argument("--started-at", type=float, help="epoch seconds the deploy went live")
    parser.add_argument("--expect", default="", help="live-sha: the commit that must be live")
    args = parser.parse_args(argv)
    out = Path(args.out)
    commit = os.environ.get("SMOKE_EXPECTED_SHA") or os.environ.get("GITHUB_SHA") or ""

    if args.mode == "live-sha":
        # The cycle's commit: the one asked for (a deploy's sha, a dispatch
        # input) once live serves it, or else whatever live serves now.
        # Prints ``sha=<full sha>`` for $GITHUB_OUTPUT; exit 1 otherwise.
        smoke = _load_smoke(args.base)
        wait_s = float(os.environ.get("SMOKE_READY_WAIT_S") or 1200)
        try:
            if args.expect.strip():
                sha = wait_for_live_sha(smoke, args.expect, wait_s=wait_s)
            else:
                sha = read_live_sha(smoke) or ""
                if not sha:
                    sha = wait_for_live_sha(smoke, "", wait_s=0)
        except CycleError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        print(f"sha={sha}")
        return 0

    config = load_config(args.config)

    if args.mode == "plan":
        # What a cycle on this commit would build -- no server touched.
        try:
            cycle_plan = plan_cycle(config, commit)
        except CycleError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "rotation": cycle_plan["rotation"],
            "runs": [{k: r[k] for k in ("name", "role", "level", "intake")} for r in cycle_plan["runs"]],
        }, indent=1))
        return 0

    if args.mode == "repros":
        try:
            cycle_plan = plan_cycle(config, commit)
        except CycleError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        print(f"rotation: {json.dumps(cycle_plan['rotation'])}", flush=True)
        smoke = _load_smoke(args.base)
        if not smoke.wait_for_ready():
            print("the deployed commit never became ready; no repro started")
            return 1
        started = time.time()
        results = run_parallel(_repro_tasks(smoke, config, cycle_plan["runs"], out))
        _write(out, REPROS_NAME, {
            "started_at": started, "finished_at": time.time(),
            "rotation": cycle_plan["rotation"], "repros": results,
        })
        return 0 if all(_repro_passed(r) for r in results.values()) else 1

    if args.mode == "report":
        recorded = json.loads((out / REPROS_NAME).read_text(encoding="utf-8")) if (out / REPROS_NAME).is_file() else {}
        report = build_report(
            smoke_a={"status": args.smoke_a or "fail", "account_index": SMOKE_ACCOUNT_INDEX},
            repros=recorded.get("repros") or {},
            smoke_b={"status": args.smoke_b or "fail", "account_index": SMOKE_ACCOUNT_INDEX},
            started_at=args.started_at or recorded.get("started_at") or time.time(),
            finished_at=time.time(),
            commit=commit,
            live_sha=read_live_sha(_load_smoke(args.base)),
            rotation=recorded.get("rotation"),
        )
        return _finish(out, report)

    # full: smoke A beside the repros, then smoke B, one process.
    cycle_plan = plan_cycle(config, commit)
    smoke = _load_smoke(args.base)
    started = time.time()
    tasks = {"smoke_a": lambda: _smoke_subprocess(args.base)}
    tasks.update(_repro_tasks(smoke, config, cycle_plan["runs"], out))
    results = run_parallel(tasks)
    smoke_a = results.pop("smoke_a")
    smoke_b = _smoke_subprocess(args.base)
    report = build_report(
        smoke_a=smoke_a, repros=results, smoke_b=smoke_b,
        started_at=started, finished_at=time.time(),
        commit=commit, live_sha=read_live_sha(smoke),
        rotation=cycle_plan["rotation"],
    )
    return _finish(out, report)


if __name__ == "__main__":
    sys.exit(main())
