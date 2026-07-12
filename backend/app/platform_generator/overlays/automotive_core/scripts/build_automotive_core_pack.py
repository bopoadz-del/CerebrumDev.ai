"""CLI to build the automotive_core_rag_v1 foundation pack.

Example:
    cd generated/automotive-safety-intelligence
    python -m scripts.build_automotive_core_pack \
        --records storage/automotive_core_rag_v1/<harvest>/canonical/recalls.jsonl \
        --output storage/automotive_core_rag_v1/<harvest> \
        --project-id automotive_core_v1
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from app.core.automotive_pack_builder import build_automotive_core_pack

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Automotive Core RAG foundation pack")
    parser.add_argument("--records", required=True, type=Path, help="Path to canonical recalls.jsonl")
    parser.add_argument("--output", required=True, type=Path, help="Output directory for pack artifacts")
    parser.add_argument("--project-id", default="automotive_core_v1", help="Vector-store project id")
    parser.add_argument("--dry-run", action="store_true", help="Compile chunks without embedding/indexing")
    args = parser.parse_args(argv)

    if not args.records.exists():
        logger.error("Canonical records not found: %s", args.records)
        return 1

    manifest = build_automotive_core_pack(
        canonical_records_path=args.records,
        output_dir=args.output,
        project_id=args.project_id,
        dry_run=args.dry_run,
    )
    print(manifest.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
