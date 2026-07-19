"""CLI: python -m app.factory.cli generate --blueprint PATH --out DIR"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.generator import ProductGenerator, git_head
from app.factory.planner import CapabilityPlanner


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

    args = parser.parse_args(argv)
    bp = load_blueprint(args.blueprint)
    blocks_root = Path(args.blocks_root).resolve() if args.blocks_root else None

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
        print(json.dumps({"ok": True, "inputs_hash": result["inputs_hash"], "output_dir": result["output_dir"]}, indent=2))
        return 0
    except DualRegistryError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
