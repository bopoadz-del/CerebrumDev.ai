"""Action service: registry, schema-driven context enrichment, execute + audit.

The enrichment layer is *generic and schema-driven*: it populates declared
input properties (records / rows / rules / rule_records / evidence) from the
project's ingested documents. It contains no per-action-id branching, so a new
action that declares e.g. a ``records`` input automatically receives context.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.retailops.audit import persist_action_run
from app.retailops.kit.contract import (
    ActionContext,
    ActionRegistry,
    ActionResult,
    ActionSpec,
    execute_action,
)
from app.retailops.models import Document, DocumentChunk
from app.retailops.retrieval import hybrid_search

# Generic mapping from a declared input property to a project record source.
# Adding an action that declares any of these inputs reuses this config; no
# action-specific code path is required.
_RULE_ROLES = {"rules", "promotion_rules"}
_EVIDENCE_ROLES = {"evidence", "promotion_evidence", "campaign_evidence"}


@lru_cache(maxsize=1)
def build_registry() -> ActionRegistry:
    """Build the action registry from the vendored (installed) domain kit."""
    registry = ActionRegistry()
    registry.discover(kit_ids=["retail"], package="app.retailops.kit.domains")
    return registry


# ---------------------------------------------------------------------------
# Record sources
# ---------------------------------------------------------------------------

def _chunk_to_record(chunk: DocumentChunk) -> Dict[str, Any]:
    record = {
        "text": chunk.text,
        "document_id": chunk.document_id,
        "filename": chunk.source_filename,
        "page": chunk.page,
        "sheet": chunk.sheet,
        "row_start": chunk.row_start,
        "row_end": chunk.row_end,
        "chunk_id": chunk.id,
    }
    # Drop null-valued keys so typed input schemas (e.g. page:int) validate.
    record = {k: v for k, v in record.items() if v is not None}
    if isinstance(chunk.meta, dict) and chunk.meta.get("rows"):
        record["rows"] = chunk.meta["rows"]
    return record


def gather_records(
    session: Session,
    tenant_id: str,
    project_id: str,
    *,
    query: Optional[str] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    if query:
        cites = hybrid_search(
            session, tenant_id=tenant_id, project_id=project_id, query=query, top_k=limit
        )
        ids = [c.chunk_id for c in cites]
        if ids:
            chunks = session.execute(
                select(DocumentChunk).where(DocumentChunk.id.in_(ids))
            ).scalars().all()
            by_id = {c.id: c for c in chunks}
            return [_chunk_to_record(by_id[i]) for i in ids if i in by_id]
    stmt = (
        select(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.project_id == project_id,
        )
        .order_by(DocumentChunk.document_id, DocumentChunk.chunk_ordinal)
        .limit(limit)
    )
    return [_chunk_to_record(c) for c in session.execute(stmt).scalars().all()]


def gather_table_rows(
    session: Session, tenant_id: str, project_id: str
) -> List[Dict[str, Any]]:
    stmt = select(DocumentChunk).where(
        DocumentChunk.tenant_id == tenant_id,
        DocumentChunk.project_id == project_id,
    )
    records: List[Dict[str, Any]] = []
    for chunk in session.execute(stmt).scalars().all():
        if isinstance(chunk.meta, dict) and chunk.meta.get("rows"):
            records.append(_chunk_to_record(chunk))
    return records


def gather_records_by_role(
    session: Session, tenant_id: str, project_id: str, roles: set
) -> List[Dict[str, Any]]:
    doc_stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.project_id == project_id,
    )
    doc_ids = [
        d.id
        for d in session.execute(doc_stmt).scalars().all()
        if isinstance(d.meta, dict) and d.meta.get("doc_role") in roles
    ]
    if not doc_ids:
        return []
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id.in_(doc_ids))
        .order_by(DocumentChunk.document_id, DocumentChunk.chunk_ordinal)
    )
    return [_chunk_to_record(c) for c in session.execute(stmt).scalars().all()]


# ---------------------------------------------------------------------------
# Schema-driven enrichment
# ---------------------------------------------------------------------------

def enrich_arguments(
    session: Session,
    spec: ActionSpec,
    context: ActionContext,
    arguments: Dict[str, Any],
    *,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """Fill declared input properties from project documents when absent."""
    props = set((spec.input_schema or {}).get("properties", {}).keys())
    args = dict(arguments)

    if "records" in props and not args.get("records"):
        args["records"] = gather_records(
            session, context.tenant_id, context.project_id, query=query
        )
    if "rows" in props and not args.get("rows"):
        role_records = gather_table_rows(session, context.tenant_id, context.project_id)
        if role_records:
            # Flatten rows from table records into a single rows list.
            rows: List[Dict[str, Any]] = []
            for rec in role_records:
                rows.extend(rec.get("rows", []))
            args["rows"] = rows
            args.setdefault("records", role_records)
    if "rule_records" in props and not args.get("rule_records") and not args.get("rules"):
        args["rule_records"] = gather_records_by_role(
            session, context.tenant_id, context.project_id, _RULE_ROLES
        )
    if "evidence" in props and not args.get("evidence"):
        args["evidence"] = gather_records_by_role(
            session, context.tenant_id, context.project_id, _EVIDENCE_ROLES
        )

    # Generic defaulting for required enum inputs that a chat/orchestrator flow
    # cannot otherwise supply: pick an option named in the query, else the first.
    schema = spec.input_schema or {}
    properties = schema.get("properties", {})
    q_tokens = set((query or "").lower().split())
    for req in schema.get("required", []):
        if req in args:
            continue
        sub = properties.get(req, {})
        options = sub.get("enum")
        if options:
            chosen = next((o for o in options if str(o).lower() in q_tokens), options[0])
            args[req] = chosen
    return args


# ---------------------------------------------------------------------------
# Execute + audit
# ---------------------------------------------------------------------------

async def execute_and_audit(
    session: Session,
    spec: ActionSpec,
    context: ActionContext,
    arguments: Dict[str, Any],
    *,
    confirmed: bool = False,
) -> ActionResult:
    result = await execute_action(spec, context, arguments, confirmed=confirmed)
    persist_action_run(session, result=result, context=context, arguments=arguments)
    return result
