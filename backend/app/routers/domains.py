from fastapi import APIRouter, HTTPException
from ..core.domain_loader import list_available_domains

router = APIRouter()


@router.get("/")
async def get_domains():
    """List available domain kits from the Cerebrum store."""
    domains = await list_available_domains()
    if not domains:
        raise HTTPException(status_code=503, detail="Domain store unreachable")
    return {"domains": domains}
