from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..core.domain_loader import list_available_domains
from ..core.rag_activation import build_rag_activation_status
from ..core.rag_ingestion_store import (
    get_acquisition_report,
    get_job,
    list_acquisition_reports,
    list_jobs,
    save_job,
    save_source_record,
)
from ..core.rag_ingestion_validation import create_ingestion_job
from ..core.rag_pack_loader import (
    RagPackLoaderError,
    list_rag_packs,
)
from ..core.rag_source_acquisition import (
    AcquisitionError,
    run_acquisition_preview,
)
from ..core.source_pack_loader import (
    SourcePackLoaderError,
    list_source_packs,
)
from ..core.virgin_shelf_loader import (
    VirginShelfLoaderError,
    list_virgin_domains,
)
from ..models.rag_ingestion import RagSourceRecord, SourceClass

router = APIRouter()


class CreateIngestionJobRequest(BaseModel):
    """Request body for creating a dry-run RAG ingestion job."""

    rag_pack_id: str
    collection_id: str
    source_class: SourceClass
    title: str
    source_uri: str
    publisher: Optional[str] = None
    license_name: Optional[str] = None
    license_uri: Optional[str] = None
    license_review_status: Optional[str] = None
    authority_rating: Optional[str] = None
    content_hash: Optional[str] = None
    external_document_id: Optional[str] = None
    dry_run: bool = True
    queue: bool = False


class AcquisitionPreviewRequest(BaseModel):
    """Request body for running an acquisition preview."""

    dry_run: bool = True
    parse: bool = True


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


@router.get("/{domain_id}/rag-activation")
async def get_rag_activation(domain_id: str):
    """Return the activation/status contract for a domain's prebuilt RAG pack.

    Read-only metadata status. Does not ingest documents, create embeddings,
    or change chain generation.
    """
    try:
        return build_rag_activation_status(domain_id)
    except RagPackLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{domain_id}/rag-ingestion/jobs")
async def create_rag_ingestion_job(
    domain_id: str, request: CreateIngestionJobRequest
):
    """Create a dry-run RAG ingestion job (validation + optional queue).

    Actual ingestion is not enabled. Use ``dry_run=true`` to validate or queue
    a proposed source record without downloading, parsing, embedding, or writing
    to a vector store.
    """
    if not request.dry_run:
        raise HTTPException(
            status_code=409,
            detail="Actual ingestion is not enabled. Use dry_run=true.",
        )

    record = RagSourceRecord(
        rag_pack_id=request.rag_pack_id,
        collection_id=request.collection_id,
        domain=domain_id,
        source_class=request.source_class,
        title=request.title,
        source_uri=request.source_uri,
        publisher=request.publisher,
        license_name=request.license_name,
        license_uri=request.license_uri,
        license_review_status=request.license_review_status,
        authority_rating=request.authority_rating,
        content_hash=request.content_hash,
        external_document_id=request.external_document_id,
    )

    # Persist the source record before validation so duplicate checks work.
    save_source_record(record)

    job = create_ingestion_job(record, dry_run=request.dry_run, queue=request.queue)
    save_job(job)
    return job.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/jobs/{job_id}")
async def get_rag_ingestion_job(domain_id: str, job_id: str):
    """Retrieve a single ingestion job, scoped to its domain."""
    job = get_job(domain_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/jobs")
async def list_rag_ingestion_jobs(
    domain_id: str,
    status: Optional[str] = Query(None),
    rag_pack_id: Optional[str] = Query(None),
    collection_id: Optional[str] = Query(None),
):
    """List ingestion jobs for a domain, with optional filters."""
    jobs = list_jobs(
        domain_id, status=status, rag_pack_id=rag_pack_id, collection_id=collection_id
    )
    return {"domain": domain_id, "jobs": [j.model_dump(mode="json") for j in jobs]}


@router.post("/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-preview")
async def create_acquisition_preview(
    domain_id: str,
    job_id: str,
    request: AcquisitionPreviewRequest,
):
    """Fetch and preview-parse the source for an eligible dry-run ingestion job.

    Does not chunk, embed, index, or persist raw source bytes. Actual
    acquisition is rejected; use ``dry_run=true``.
    """
    try:
        report = run_acquisition_preview(
            domain_id=domain_id,
            job_id=job_id,
            dry_run=request.dry_run,
            parse=request.parse,
        )
    except AcquisitionError as exc:
        status_code = 409 if exc.code == "DRY_RUN_REQUIRED" else 422
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "field": exc.field},
        ) from exc
    return report.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-previews")
async def list_acquisition_previews(domain_id: str, job_id: str):
    """List acquisition preview reports for a job, scoped to its domain."""
    reports = list_acquisition_reports(domain_id, job_id=job_id)
    return {"domain": domain_id, "job_id": job_id, "acquisitions": [r.model_dump(mode="json") for r in reports]}


@router.get(
    "/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-previews/{acquisition_id}"
)
async def get_acquisition_preview(domain_id: str, job_id: str, acquisition_id: str):
    """Retrieve a single acquisition preview report, scoped to its domain."""
    report = get_acquisition_report(domain_id, acquisition_id)
    if report is None or report.job_id != job_id:
        raise HTTPException(status_code=404, detail="Acquisition preview not found")
    return report.model_dump(mode="json")
