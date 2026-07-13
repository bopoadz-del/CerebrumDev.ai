"""Generic action + RAG + document + audit HTTP API for the RetailOps runtime.

Endpoints:
    GET  /v1/actions
    GET  /v1/actions/{action_id}
    POST /v1/actions/{action_id}/execute
    POST /v1/ask                      (LangGraph orchestration)
    POST /v1/documents                (ingest)
    GET  /v1/documents
    GET  /v1/audit
    GET  /v1/overview
    GET  /health

The execute route is fully generic: adding an action to a kit needs no router
change. Trusted context (tenant/project/permissions) is resolved server-side
from the authenticated principal and never from the request body.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.retailops import service
from app.retailops.audit import list_action_runs, persist_action_run
from app.retailops.db import get_session
from app.retailops.kit.contract import ActionContext, ActionResult, ActionStatus
from app.retailops.kit.contract.models import ActionResult as _AR
from app.retailops.models import Document, User
from app.retailops.orchestrator import run_orchestrator

router = APIRouter()

# Domains installed in this generated product (server-controlled allow-list).
INSTALLED_DOMAINS = ["retail"]


# ---------------------------------------------------------------------------
# Trusted context resolution
# ---------------------------------------------------------------------------

def get_context(
    session: Session = Depends(get_session),
    x_user_id: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None),
    x_project_id: Optional[str] = Header(None),
) -> ActionContext:
    if not (x_user_id and x_tenant_id and x_project_id):
        raise HTTPException(status_code=401, detail="missing X-User-Id/X-Tenant-Id/X-Project-Id")
    user = session.get(User, x_user_id)
    if user is None or user.tenant_id != x_tenant_id:
        raise HTTPException(status_code=403, detail="unknown principal for tenant")
    # Permissions are read from the trusted user record, never from the client.
    return ActionContext(
        user_id=user.id,
        tenant_id=x_tenant_id,
        organisation_id=x_tenant_id,
        project_id=x_project_id,
        permissions=list(user.permissions or []),
        allowed_domains=list(INSTALLED_DOMAINS),
        corpus_id=x_project_id,
        knowledge_layer="project",
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)
    auto_context: bool = True
    confirm: bool = False


class AskRequest(BaseModel):
    question: str


class IngestRequest(BaseModel):
    filename: str
    content_base64: Optional[str] = None
    text: Optional[str] = None
    content_type: Optional[str] = None
    doc_role: Optional[str] = None


# ---------------------------------------------------------------------------
# Action discovery + execution
# ---------------------------------------------------------------------------

@router.get("/v1/actions")
def list_actions(context: ActionContext = Depends(get_context)) -> Dict[str, Any]:
    registry = service.build_registry()
    return {"actions": [s.public_dict() for s in registry.list_permitted(context)]}


@router.get("/v1/actions/{action_id}")
def get_action(action_id: str, context: ActionContext = Depends(get_context)) -> Dict[str, Any]:
    registry = service.build_registry()
    spec = registry.resolve(action_id)
    if spec is None or not context.allows_domain(spec.domain):
        raise HTTPException(status_code=404, detail={"status": "unsupported", "action_id": action_id})
    return spec.public_dict()


def _unsupported_result(action_id: str) -> ActionResult:
    now = datetime.now(timezone.utc)
    return _AR(
        request_id=f"act_{uuid.uuid4().hex}",
        action_id=action_id if "." in action_id else f"unknown.{action_id}",
        status=ActionStatus.UNSUPPORTED,
        started_at=now,
        completed_at=now,
        duration_ms=0,
        error_code="unsupported_action",
        error_message=f"action '{action_id}' is not registered",
    )


@router.post("/v1/actions/{action_id}/execute")
async def execute(
    action_id: str,
    body: ExecuteRequest,
    session: Session = Depends(get_session),
    context: ActionContext = Depends(get_context),
) -> Dict[str, Any]:
    registry = service.build_registry()
    spec = registry.resolve(action_id)
    if spec is None:
        result = _unsupported_result(action_id)
        return result.to_dict()

    arguments = dict(body.arguments or {})
    if body.auto_context:
        arguments = service.enrich_arguments(session, spec, context, arguments)
    result = await service.execute_and_audit(
        session, spec, context, arguments, confirmed=body.confirm
    )
    session.commit()
    return result.to_dict()


# ---------------------------------------------------------------------------
# RAG / orchestration
# ---------------------------------------------------------------------------

@router.post("/v1/ask")
async def ask(
    body: AskRequest,
    session: Session = Depends(get_session),
    context: ActionContext = Depends(get_context),
) -> Dict[str, Any]:
    outcome = await run_orchestrator(
        session=session, context=context, question=body.question
    )
    session.commit()
    result = outcome.get("action_result")
    return {
        "route": outcome["route"],
        "action_id": outcome.get("action_id"),
        "answer": outcome["answer"],
        "citations": outcome.get("citations", []),
        "action_result": result.to_dict() if result is not None else None,
    }


# ---------------------------------------------------------------------------
# Documents / ingestion
# ---------------------------------------------------------------------------

@router.post("/v1/documents")
def ingest(
    body: IngestRequest,
    session: Session = Depends(get_session),
    context: ActionContext = Depends(get_context),
) -> Dict[str, Any]:
    import base64

    from app.retailops.ingestion import ingest_document

    if body.content_base64:
        data = base64.b64decode(body.content_base64)
    elif body.text is not None:
        data = body.text.encode("utf-8")
    else:
        raise HTTPException(status_code=400, detail="provide content_base64 or text")

    doc = ingest_document(
        session,
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        filename=body.filename,
        data=data,
        content_type=body.content_type,
    )
    if body.doc_role:
        meta = dict(doc.meta or {})
        meta["doc_role"] = body.doc_role
        doc.meta = meta
    session.commit()
    return {
        "document_id": doc.id,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "page_count": doc.page_count,
        "filename": doc.filename,
    }


@router.get("/v1/documents")
def list_documents(
    session: Session = Depends(get_session),
    context: ActionContext = Depends(get_context),
) -> Dict[str, Any]:
    stmt = (
        select(Document)
        .where(
            Document.tenant_id == context.tenant_id,
            Document.project_id == context.project_id,
        )
        .order_by(Document.created_at.desc())
    )
    docs = session.execute(stmt).scalars().all()
    return {
        "documents": [
            {
                "document_id": d.id,
                "filename": d.filename,
                "status": d.status,
                "chunk_count": d.chunk_count,
                "page_count": d.page_count,
                "content_type": d.content_type,
                "doc_role": (d.meta or {}).get("doc_role"),
                "error": d.error,
            }
            for d in docs
        ]
    }


# ---------------------------------------------------------------------------
# Audit + overview
# ---------------------------------------------------------------------------

@router.get("/v1/audit")
def audit(
    session: Session = Depends(get_session),
    context: ActionContext = Depends(get_context),
) -> Dict[str, Any]:
    runs = list_action_runs(session, tenant_id=context.tenant_id, project_id=context.project_id)
    return {
        "runs": [
            {
                "request_id": r.request_id,
                "action_id": r.action_id,
                "user_id": r.user_id,
                "project_id": r.project_id,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "duration_ms": r.duration_ms,
                "evidence_count": r.evidence_count,
                "kit_version": r.kit_version,
                "error_code": r.error_code,
            }
            for r in runs
        ]
    }


@router.get("/v1/overview")
def overview(
    session: Session = Depends(get_session),
    context: ActionContext = Depends(get_context),
) -> Dict[str, Any]:
    doc_count = session.execute(
        select(func.count(Document.id)).where(
            Document.tenant_id == context.tenant_id,
            Document.project_id == context.project_id,
        )
    ).scalar_one()
    runs = list_action_runs(
        session, tenant_id=context.tenant_id, project_id=context.project_id, limit=5
    )
    return {
        "project_id": context.project_id,
        "document_count": doc_count,
        "recent_actions": [
            {"action_id": r.action_id, "status": r.status, "started_at": r.started_at.isoformat()}
            for r in runs
        ],
        "system_health": {"database": "ok", "kit_domains": INSTALLED_DOMAINS},
    }
