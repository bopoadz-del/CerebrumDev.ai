from fastapi import APIRouter, HTTPException
from ..core.domain_loader import list_available_domains
from ..core.rag_pack_loader import (
    RagPackLoaderError,
    list_rag_packs,
)
from ..core.source_pack_loader import (
    SourcePackLoaderError,
    list_source_packs,
)
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


@router.get("/source-packs")
async def get_source_packs():
    """List Domain Source Pack metadata from the Cerebrum-Blocks shelf.

    Read-only: exposes how each domain should think, respond, and wire blocks.
    Does not affect runtime chain generation.
    """
    try:
        packs = list_source_packs()
    except SourcePackLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"shelf_id": "source_packs", "packs": packs}


@router.get("/rag-packs")
async def get_rag_packs():
    """List Prebuilt Domain RAG Pack metadata from the Cerebrum-Blocks shelf.

    Read-only: exposes what prebuilt domain knowledge collections exist.
    Does not perform ingestion, create embeddings, or affect runtime behavior.
    """
    try:
        packs = list_rag_packs()
    except RagPackLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"shelf_id": "rag_packs", "packs": packs}
