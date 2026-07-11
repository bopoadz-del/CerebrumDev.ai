"""CLI to generate the Automotive Safety Intelligence Pilot platform package.

Example:
    cd backend
    python -m scripts.generate_automotive_platform \
        --manifest ../docs/superpowers/fixtures/automotive_platform_manifest.json \
        --output ../generated/automotive-safety-intelligence
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from app.platform_generator import PlatformGenerator

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a Fork-derived automotive platform package"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/superpowers/fixtures/automotive_platform_manifest.json"),
        help="Path to platform manifest JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for the generated package",
    )
    parser.add_argument(
        "--fork-path",
        type=Path,
        default=None,
        help="Path to The_Fork repository (defaults to ../The_Fork or FORK_BASELINE_PATH env)",
    )
    parser.add_argument(
        "--blocks-path",
        type=Path,
        default=None,
        help="Path to Cerebrum-Blocks repository (defaults to ../Cerebrum-Blocks or CEREBRUM_BLOCKS_PATH env)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete output directory if it already exists",
    )
    args = parser.parse_args(argv)

    if not args.manifest.exists():
        logger.error("Manifest not found: %s", args.manifest)
        return 1

    if args.output.exists():
        if not args.force:
            logger.error("Output directory already exists: %s", args.output)
            return 1
        shutil.rmtree(args.output)

    manifest_data = _load_manifest(args.manifest)
    generator = PlatformGenerator.from_dict(
        manifest_data,
        fork_path=args.fork_path,
        blocks_path=args.blocks_path,
    )
    inputs_hash = generator.generate(args.output)

    logger.info("Generation complete.")
    logger.info("  Output: %s", args.output.resolve())
    logger.info("  Fork commit: %s", inputs_hash.fork_commit)
    logger.info("  Blocks commit: %s", inputs_hash.blocks_commit)
    logger.info("  Inputs hash: %s", inputs_hash.hash)
    return 0


if __name__ == "__main__":
    sys.exit(main())
