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


def _resolve_blocks_root(cli_value: str | None) -> Path | None:
    if cli_value:
        return Path(cli_value).resolve()
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    return Path(env).resolve() if env else None


def main(argv: list[str] | None = None) -> int:
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

    p_store = sub.add_parser("store", help="Block Store Manager tools")
    store_sub = p_store.add_subparsers(dest="store_cmd", required=True)
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


def _store_cmd(args: argparse.Namespace) -> int:
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
