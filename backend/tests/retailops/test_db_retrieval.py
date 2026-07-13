"""Migrations, pgvector insertion/retrieval, lexical retrieval, RRF, isolation."""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.retailops import bootstrap
from app.retailops.ingestion import ingest_document
from app.retailops.retrieval import hybrid_search


def _seed_project(session, tenant, canonical):
    bootstrap.ensure_tenant(session, tenant, tenant)
    return bootstrap.ensure_project(session, tenant_id=tenant, canonical_name=canonical).id


def test_migrations_created_tables(db):
    insp = inspect(db.get_bind())
    tables = set(insp.get_table_names())
    assert {
        "tenants", "users", "projects", "documents",
        "document_chunks", "action_runs", "conversations", "messages",
    }.issubset(tables)


def test_pgvector_insertion_and_semantic_retrieval(db):
    pid = _seed_project(db, "t1", "P1")
    ingest_document(
        db, tenant_id="t1", project_id=pid, filename="a.txt",
        data=b"Stock discrepancy escalation: notify the duty manager within 15 minutes.",
        content_type="text/plain",
    )
    ingest_document(
        db, tenant_id="t1", project_id=pid, filename="b.txt",
        data=b"The store cafe serves espresso and pastries in the morning.",
        content_type="text/plain",
    )
    db.commit()
    # embedding column is a real pgvector column with data.
    cnt = db.execute(text("SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL")).scalar_one()
    assert cnt == 2
    cites = hybrid_search(db, tenant_id="t1", project_id=pid, query="how to escalate a stock discrepancy")
    assert cites
    assert "discrepancy" in cites[0].excerpt.lower()
    assert cites[0].vector_score is not None


def test_lexical_retrieval_and_rrf(db):
    pid = _seed_project(db, "t1", "P1")
    ingest_document(
        db, tenant_id="t1", project_id=pid, filename="c.txt",
        data=b"Promotional price cards must be visible on the shelf for every promoted SKU.",
        content_type="text/plain",
    )
    db.commit()
    cites = hybrid_search(db, tenant_id="t1", project_id=pid, query="promotional price cards shelf")
    assert cites
    top = cites[0]
    assert top.lexical_score is not None and top.lexical_score > 0
    assert top.fused_score > 0 and top.fused_rank == 1


def test_project_isolation(db):
    p1 = _seed_project(db, "t1", "P1")
    p2 = _seed_project(db, "t1", "P2")
    ingest_document(
        db, tenant_id="t1", project_id=p1, filename="secret.txt",
        data=b"Project one confidential supplier notice about chilled dairy delay.",
        content_type="text/plain",
    )
    db.commit()
    # Querying project 2 must never see project 1's chunk.
    cites = hybrid_search(db, tenant_id="t1", project_id=p2, query="chilled dairy supplier notice")
    assert cites == []


def test_tenant_isolation(db):
    p1 = _seed_project(db, "tenantA", "Shared Name")
    p2 = _seed_project(db, "tenantB", "Shared Name")
    ingest_document(
        db, tenant_id="tenantA", project_id=p1, filename="a.txt",
        data=b"Tenant A private inventory anomaly report.",
        content_type="text/plain",
    )
    db.commit()
    cites = hybrid_search(db, tenant_id="tenantB", project_id=p2, query="inventory anomaly report")
    assert cites == []
