"""CLI: python -m app.factory.cli generate|plan|store ..."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.generator import ProductGenerator, git_head
from app.factory.planner import CapabilityPlanner
from app.factory.store_manager import (
    ImprovementClass,
    PromotionDecision,
    StoreOp,
    evaluate_publish_gates,
    health_cycle_steps,
    store_manager_manifest,
)


def _load_backend_env() -> None:
    """Load backend/.env into the environment, without overriding it.

    The API server gets its environment from the platform (Render) or the
    operator's shell; this CLI is run directly, and on the first live build
    the coder silently fell back to templates on every artifact because the
    configured key sat in backend/.env, which nothing loaded. Existing
    environment variables win -- this only fills gaps.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def _resolve_blocks_root(cli_value: str | None) -> Path | None:
    if cli_value:
        return Path(cli_value).resolve()
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    return Path(env).resolve() if env else None


def main(argv: list[str] | None = None) -> int:
    _load_backend_env()
    parser = argparse.ArgumentParser(prog="cerebrum-factory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Plan capabilities from a blueprint")
    p_plan.add_argument("--blueprint", required=True)
    p_plan.add_argument("--blocks-root", default=None)

    p_gen = sub.add_parser("generate", help="Generate a product repository")
    p_gen.add_argument("--blueprint", required=True)
    p_gen.add_argument("--out", required=True)
    p_gen.add_argument("--blocks-root", default=None)
    p_gen.add_argument("--no-clean", action="store_true")

    p_build = sub.add_parser(
        "build",
        help="Build a platform through the role runner (agent-manufactured)",
    )
    p_build.add_argument("--blueprint", required=True)
    p_build.add_argument("--out", required=True)
    p_build.add_argument("--blocks-root", default=None)
    p_build.add_argument(
        "--max-rework",
        type=int,
        default=3,
        help="writer/tester rounds before the run fails on budget",
    )
    p_build.add_argument(
        "--wall-clock",
        type=float,
        default=7200.0,
        help="seconds before the run fails on budget (0 disables)",
    )
    p_build.add_argument(
        "--phase-wall-clock",
        type=float,
        default=1500.0,
        help="seconds each role may spend (0 disables; Floor default 25 min)",
    )

    p_store = sub.add_parser("store", help="Block Store Manager tools")
    store_sub = p_store.add_subparsers(dest="store_cmd", required=True)
    p_registry = store_sub.add_parser(
        "registry",
        help="Read-only registrar: what each platform cloned, and what has drifted",
    )
    p_registry.add_argument(
        "--root",
        required=True,
        help="directory containing built platforms (scanned for build ledgers)",
    )
    p_registry.add_argument(
        "--store-head",
        default=None,
        help="Store commit to compare against; without it every clone is 'unknown'",
    )
    p_registry.add_argument(
        "--stale-only",
        action="store_true",
        help="exit non-zero if any clone is stale",
    )

    store_sub.add_parser("manifest", help="Print Store Manager authority manifest")
    store_sub.add_parser("health-scan", help="Print Store Health Cycle steps")
    p_classify = store_sub.add_parser("classify", help="Classify a product vs Store diff")
    p_classify.add_argument("--block-id", required=True)
    p_classify.add_argument(
        "--class",
        dest="improvement_class",
        required=True,
        choices=[c.value for c in ImprovementClass],
    )
    p_classify.add_argument(
        "--decision",
        required=True,
        choices=[d.value for d in PromotionDecision],
    )
    p_decide = store_sub.add_parser("decide", help="Evaluate publish gates for a Store op")
    p_decide.add_argument(
        "--op",
        required=True,
        choices=[o.value for o in StoreOp],
    )
    p_decide.add_argument("--user-approved", action="store_true")
    p_decide.add_argument(
        "--checklist-json",
        default="{}",
        help="JSON object of checklist booleans",
    )

    args = parser.parse_args(argv)

    if args.cmd == "store":
        return _store_cmd(args)

    bp = load_blueprint(args.blueprint)
    blocks_root = _resolve_blocks_root(getattr(args, "blocks_root", None))

    if args.cmd == "build":
        return _build_cmd(args, bp, blocks_root)

    try:
        if args.cmd == "plan":
            plan = CapabilityPlanner(blocks_root).plan(bp)
            print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
            return 0
        factory_root = Path(__file__).resolve().parents[3]
        blocks_commit = git_head(blocks_root) if blocks_root else "unknown"
        gen = ProductGenerator(
            bp,
            blocks_root=blocks_root,
            factory_commit=git_head(factory_root),
            blocks_commit=blocks_commit,
        )
        result = gen.generate(args.out, clean=not args.no_clean)
        print(
            json.dumps(
                {
                    "ok": True,
                    "inputs_hash": result["inputs_hash"],
                    "output_dir": result["output_dir"],
                    "resident_engineer": result.get("resident_engineer"),
                },
                indent=2,
            )
        )
        return 0
    except DualRegistryError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


def _build_cmd(args: argparse.Namespace, blueprint, blocks_root) -> int:
    """Drive a build through the role runner.

    Separate from `generate`, which is the legacy template path and remains
    what production calls. Exit 0 only on RUN_SUCCEEDED -- a run that spent
    its budget or failed a gate exits non-zero, so a CI or shell caller
    cannot mistake a failed build for a delivered one.
    """
    from app.factory.build.runner import BuildBudget, RoleRunner

    runner = RoleRunner(
        blueprint,
        args.out,
        blocks_root=blocks_root,
        budget=BuildBudget(
            max_rework=args.max_rework,
            wall_clock_s=args.wall_clock,
            phase_wall_clock_s=args.phase_wall_clock,
        ),
    )
    outcome = runner.run()
    sources = runner.state.get("artifact_sources", {})
    by_agent = sorted(k for k, v in sources.items() if v.startswith("coder LLM"))
    print(
        json.dumps(
            {
                **outcome.to_dict(),
                "artifacts": len(sources),
                "agent_written": len(by_agent),
                "agent_artifacts": by_agent,
                "coder_failures": runner.state.get("coder_failures", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if outcome.ok else 1


def _store_cmd(args: argparse.Namespace) -> int:
    if args.store_cmd == "registry":
        from app.factory.build.registrar import registrar_report

        report = registrar_report(args.root, store_head=args.store_head)
        print(json.dumps(report, indent=2, sort_keys=True))
        # Read-only: exit 0 unless the caller explicitly asked to be told
        # about drift. "unknown" is never treated as a failure -- it means the
        # comparison could not be made, not that anything is wrong.
        if args.stale_only and report["status_counts"].get("stale"):
            return 2
        return 0
    if args.store_cmd == "manifest":
        print(json.dumps(store_manager_manifest(), indent=2, sort_keys=True))
        return 0
    if args.store_cmd == "health-scan":
        print(json.dumps({"steps": health_cycle_steps()}, indent=2))
        return 0
    if args.store_cmd == "classify":
        print(
            json.dumps(
                {
                    "block_id": args.block_id,
                    "classification": args.improvement_class,
                    "decision": args.decision,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.store_cmd == "decide":
        checklist = json.loads(args.checklist_json)
        result = evaluate_publish_gates(
            args.op, checklist, user_approved=args.user_approved
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if result.allowed else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
