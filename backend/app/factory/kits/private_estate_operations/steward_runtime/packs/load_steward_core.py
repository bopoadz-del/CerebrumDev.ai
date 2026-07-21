"""CLI: python -m app.steward.packs.load_steward_core --profile mini [--force]"""

from __future__ import annotations

import argparse
import json

from app.steward.db import init_engine, session_scope
from app.steward.migrations_runner import run_migrations
from app.steward.packs import load_full_or_fail, load_service_core_mini


def main() -> None:
    parser = argparse.ArgumentParser(description="Load steward_service_core_open_v1")
    parser.add_argument("--profile", choices=("mini", "full"), default="mini")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.profile == "full":
        load_full_or_fail("steward_service_core_open_v1")
    init_engine()
    run_migrations()
    with session_scope() as session:
        result = load_service_core_mini(session, force=args.force)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
