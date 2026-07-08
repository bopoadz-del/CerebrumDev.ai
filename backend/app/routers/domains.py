from fastapi import APIRouter, HTTPException
from ..core.domain_loader import list_available_domains
from ..core.virgin_shelf_loader import (
    VirginShelfLoaderError,
    list_virgin_domains,
)

router = APIRouter()


@router.get("/")
async def get_domains():
    """List available domain kits from the Cerebrum store."""
    domains = await list_available_domains()
    if not domains:
        raise HTTPException(status_code=503, detail="Domain store unreachable")
    return {"domains": domains}


@router.get("/virgin")
async def get_virgin_domains():
    """List Domain Virgin Edition manifests from the Cerebrum-Blocks shelf.

    Read-only: exposes what the store engine advertises as its minimal clean
    domain editions. Does not affect runtime block availability.
    """
    try:
        editions = list_virgin_domains()
    except VirginShelfLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"shelf_id": "virgin_domains", "editions": editions}
