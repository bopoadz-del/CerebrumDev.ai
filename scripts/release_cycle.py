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

    # the commit this cycle is about (prints sha=...; exit 1 unless it is live)
    python scripts/release_cycle.py live-sha [--expect <sha>]
    # the repros, beside the live-smoke job
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
DEFAULT_CONFIG = ROOT / "scripts" / "release_cycle.json"

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
    """The cycle's data: build level, target, and the repro briefs."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    repros = data.get("repros")
    if not isinstance(repros, list) or not repros:
        raise CycleError(f"{path}: 'repros' must be a non-empty list")
    for entry in repros:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            raise CycleError(f"{path}: every repro needs a 'name'")
        if not str(entry.get("brief") or "").strip():
            raise CycleError(f"{path}: repro {entry.get('name')!r} has no 'brief'")
    names = [str(r["name"]) for r in repros]
    if len(names) != len(set(names)):
        raise CycleError(f"{path}: repro names must be distinct: {names}")
    data.setdefault("build_level", "production")
    return data


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
) -> Dict[str, Any]:
    """Draft, choose the level, confirm, approve, then wait for the build's own
    terminal state and take the export. Uses the smoke's client so the flow is
    exactly the smoke's. A transient gateway answer while polling (a platform
    restart) is a reason to keep polling -- never the build's verdict."""
    started = time.monotonic()
    s, body = smoke.req("POST", "/v1/sessions/", {}, token=token)
    sid = (body or {}).get("session_id") if isinstance(body, dict) else None
    if not sid:
        return {"status": "error", "error": f"session create http={s}"}
    smoke.chat_until_drafted(sid, token, brief)
    smoke.chat(sid, token, "", action="set_build_level", value=level)
    smoke.chat(sid, token, "", action="confirm_intake")
    smoke.chat(sid, token, "", action="approve")

    transient = getattr(smoke, "TRANSIENT", {502, 503, 504})
    deadline = time.monotonic() + wait_s
    last_print = 0.0
    http, blob, build = 0, b"", {}
    while True:
        http, blob = smoke.req("GET", f"/v1/sessions/{sid}/product/package", token=token, raw=True)
        _st, status = smoke.req("GET", f"/v1/sessions/{sid}/product/build-status", token=token)
        status = status if isinstance(status, dict) else {}
        build = status.get("build") if isinstance(status.get("build"), dict) else status
        state = build.get("state")
        if http == 200:
            break
        if http not in transient and http != 409:
            break
        if http == 409 and state in {"failed", "stalled"}:
            break
        if time.monotonic() >= deadline:
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
) -> Dict[str, Any]:
    """One record of the cycle. Passes only when both smokes pass, every
    repro exported a CERTIFIED zip, and live /version still serves the commit
    the report names (``live_sha``, read when the report is written). The
    cycle's wall time is recorded, never judged."""
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
    return {
        "commit": commit,
        "live_sha": live_sha,
        "verdict": "fail" if reasons else "pass",
        "reason": "; ".join(reasons),
        "cycle_s": cycle_s,
        "runs": runs,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"## Release cycle on `{str(report.get('commit'))[:8]}`: {report['verdict'].upper()}",
        "",
        f"Cycle time {report['cycle_s'] / 60:.0f} min.",
        "",
    ]
    if report.get("reason"):
        lines += [f"Why: {report['reason']}", ""]
    lines += [
        "| Run | Account | Result | Export | Certified | Wall |",
        "|---|---|---|---|---|---|",
    ]
    for name, run in report["runs"].items():
        export = run.get("export") or {}
        manifest = export.get("manifest") or {}
        size = f"{export['bytes']:,} B / {export['files']} files" if export else "—"
        wall = f"{float(run['wall_s']) / 60:.0f} min" if run.get("wall_s") is not None else "—"
        lines.append(
            f"| {name} | {run.get('account_index', '—')} | {run.get('status')} | {size} | "
            f"{manifest.get('certified', '—') if export else '—'} | {wall} |"
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


def _repro_tasks(smoke: Any, config: Mapping[str, Any], out: Path) -> Dict[str, Callable[[], Dict[str, Any]]]:
    names = [r["name"] for r in config["repros"]]
    roster = smoke.verified_token_roster(len(names) + 1)
    plan = assign_accounts(names, available=len(roster))
    wait_s = float(getattr(smoke, "BUILD_WAIT_S", 5400))
    tasks: Dict[str, Callable[[], Dict[str, Any]]] = {}
    for repro in config["repros"]:
        name, index = repro["name"], plan["repros"][repro["name"]]

        def task(name=name, index=index, brief=repro["brief"]):
            result = drive_build(
                smoke, roster[index], brief,
                level=config["build_level"], wait_s=wait_s, label=name, save_to=out,
            )
            result["account_index"] = index
            return result

        tasks[name] = task
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
    parser.add_argument("mode", choices=("live-sha", "repros", "report", "full"))
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

    if args.mode == "repros":
        smoke = _load_smoke(args.base)
        if not smoke.wait_for_ready():
            print("the deployed commit never became ready; no repro started")
            return 1
        started = time.time()
        results = run_parallel(_repro_tasks(smoke, config, out))
        _write(out, REPROS_NAME, {"started_at": started, "finished_at": time.time(), "repros": results})
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
        )
        return _finish(out, report)

    # full: smoke A beside the repros, then smoke B, one process.
    smoke = _load_smoke(args.base)
    started = time.time()
    tasks = {"smoke_a": lambda: _smoke_subprocess(args.base)}
    tasks.update(_repro_tasks(smoke, config, out))
    results = run_parallel(tasks)
    smoke_a = results.pop("smoke_a")
    smoke_b = _smoke_subprocess(args.base)
    report = build_report(
        smoke_a=smoke_a, repros=results, smoke_b=smoke_b,
        started_at=started, finished_at=time.time(),
        commit=commit, live_sha=read_live_sha(smoke),
    )
    return _finish(out, report)


if __name__ == "__main__":
    sys.exit(main())
