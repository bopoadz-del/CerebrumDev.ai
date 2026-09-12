"""Master-key gated one-shot restore of disk SQLite accounts into Postgres.

The live engine is ``ACCOUNTS_DATABASE_URL``. When that variable was missing,
the app wrote to ``STORAGE_PATH/accounts.db`` instead. This router copies
missing rows back without wiping smoke accounts already in Postgres.

``cerebrum-builds`` is not the accounts store.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.auth import require_master_key
from scripts.migrate_accounts_to_postgres import (
    AccountsRestoreError,
    compare_stores,
    run_restore,
)

router = APIRouter(prefix="/v1/ops", tags=["ops"])


def _http_error(exc: AccountsRestoreError) -> HTTPException:
    status = {
        "accounts_url_unset": 409,
        "sqlite_missing": 404,
        "target_not_empty": 409,
        "schema_missing": 409,
        "verify_failed": 500,
    }.get(exc.code, 400)
    return HTTPException(status_code=status, detail=exc.as_dict())


@router.get("/accounts-restore")
async def list_accounts_restore(_admin=Depends(require_master_key)):
    """Compare disk SQLite vs Postgres account ids and emails (no hashes)."""
    return await asyncio.to_thread(compare_stores)


@router.post("/accounts-restore")
async def run_accounts_restore(
    force: bool = Query(False),
    dry_run: bool = Query(False),
    _admin=Depends(require_master_key),
):
    """Copy missing SQLite accounts into ACCOUNTS_DATABASE_URL Postgres.

    Default refuses a non-empty target. ``force=true`` merges: insert missing
    ids/emails only. Re-runs are idempotent. Password hashes are copied so
    login keeps working, but they are never returned.
    """

    def _run():
        try:
            return run_restore(
                force=force,
                dry_run=dry_run,
                verify=not dry_run,
            ).as_public_dict()
        except AccountsRestoreError as exc:
            raise _http_error(exc) from exc

    return await asyncio.to_thread(_run)
