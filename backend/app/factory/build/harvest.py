"""S13 upstream harvest — Cerebrum-Blocks / factory-upstream.

STORE_MANAGER's write half is unbuilt (U6). This repo has no authorized
write path to ``bopoadz-del/Cerebrum-Blocks``. Harvest must not pretend to
copy pipeline fixes upstream.

This module is the honest BLOCK: it records why harvest cannot run and
what that means for the next product. It never writes to a Store checkout.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

HARVEST_ID = "app.factory.build.harvest.evaluate_harvest"
BLOCKS_REPO = "bopoadz-del/Cerebrum-Blocks"

# Factory-owned corrections that live only in CerebrumDev.ai emission until
# an authorized harvest exists. Named so the BLOCK is about real files.
UNHARVESTED_FIXES = (
    "RoleRunner offline_adapters (Store-unwired vendor contracts)",
    "P1 capture adapter (network:false / llm_provider=none)",
    "S10 versioned Alembic + WAL store (no CREATE TABLE on connect)",
    "S11 fail-closed /health, rollback.sh, JSON request logs",
    "S12 domain_ops / work_queue through execute_action",
)

CONSEQUENCE = (
    "Products will not receive those Factory pipeline fixes from Cerebrum-Blocks "
    "until harvest exists. The next product re-ships Factory-owned corrections "
    "only if CLONER/WRITER emission in this repo stays applied. This does not "
    "pretend Blocks was fixed. No Blocks write was performed; none is authorized "
    "from this repo."
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _store_write_authorized() -> bool:
    """A harvest write would require an explicit, committed authorization.

    None exists. Environment flags are not authorization — a secret in the
    agent dashboard must not silently push to Cerebrum-Blocks.
    """
    return False


def _blocks_checkout(explicit: Optional[Path] = None) -> Optional[Path]:
    if explicit is not None:
        path = Path(explicit)
        return path if path.exists() else None
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    if env:
        path = Path(env)
        if path.exists():
            return path
    sibling = _repo_root().parent / "Cerebrum-Blocks"
    if sibling.exists():
        return sibling
    return None


def evaluate_harvest(blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return a harvest verdict. Never copies files. Never writes upstream."""
    checkout = _blocks_checkout(blocks_root)
    authorized = _store_write_authorized()
    copied: list[str] = []
    blocked = True
    reason = (
        "STORE_MANAGER harvest remains unbuilt (U6). "
        "authority.py STORE_MANAGER mandate: harvesting improvements back "
        "upstream remains unbuilt. registrar.py is read-only. No git remote "
        f"or workflow in this repo writes to {BLOCKS_REPO}."
    )
    if checkout is not None and not authorized:
        reason += (
            f" A checkout exists at {checkout} but write is not authorized; "
            "files were not copied."
        )
    return {
        "gate": HARVEST_ID,
        "verdict": "BLOCKED",
        "ok": False,
        "blocked": blocked,
        "copied": copied,
        "copied_count": 0,
        "authorized_write_path": authorized,
        "blocks_repo": BLOCKS_REPO,
        "blocks_checkout": str(checkout) if checkout else None,
        "unharvested_fixes": list(UNHARVESTED_FIXES),
        "reason": reason,
        "consequence": CONSEQUENCE,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
