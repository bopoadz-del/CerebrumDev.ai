"""Run the automotive golden-question evaluation.

Example:
  python -m scripts.run_automotive_evaluation \
    --golden backend/app/platform_generator/overlays/automotive_core/evaluation/golden_questions.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.automotive_evaluation import run_automotive_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run automotive golden evaluation")
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "app"
        / "platform_generator"
        / "overlays"
        / "automotive_core"
        / "evaluation"
        / "golden_questions.jsonl",
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    if not args.golden.exists():
        print(f"Golden questions not found: {args.golden}", file=sys.stderr)
        return 1

    report = run_automotive_evaluation(golden_path=args.golden, top_k=args.top_k)
    print(json.dumps(report, indent=2))
    return 0 if report.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
