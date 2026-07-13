"""Ingestion coverage for every pilot format + provenance."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.retailops import bootstrap
from app.retailops.ingestion import detect_type, ingest_document, parse_bytes
from app.retailops.models import Document, DocumentChunk

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "retailops" / "fixtures" / "northstar"


def test_detect_type():
    assert detect_type("a.pdf") == "pdf"
    assert detect_type("a.docx") == "docx"
    assert detect_type("a.md") == "markdown"
    assert detect_type("a.csv") == "csv"
    assert detect_type("a.xlsx") == "xlsx"
    assert detect_type("a.txt") == "txt"


def test_every_format_parses():
    samples = {
        "riyadh/sop_stock_discrepancy.pdf": "pdf",
        "riyadh/promotion_evidence.docx": "docx",
        "riyadh/promotion_rules.md": "markdown",
        "riyadh/supplier_notice.txt": "txt",
        "riyadh/inventory.csv": "csv",
        "riyadh/inventory.xlsx": "xlsx",
    }
    for rel, _ in samples.items():
        data = (FIXTURES / rel).read_bytes()
        units = parse_bytes(data, Path(rel).name)
        assert units, f"no units parsed from {rel}"
        assert any(u.text.strip() for u in units)


def test_csv_preserves_row_provenance_and_structured_rows(db):
    bootstrap.ensure_tenant(db, "t1", "t1")
    pid = bootstrap.ensure_project(db, tenant_id="t1", canonical_name="P").id
    data = (FIXTURES / "riyadh" / "inventory.csv").read_bytes()
    doc = ingest_document(db, tenant_id="t1", project_id=pid, filename="inventory.csv", data=data)
    db.commit()
    chunks = db.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    ).scalars().all()
    assert chunks
    table_chunks = [c for c in chunks if c.meta.get("rows")]
    assert table_chunks
    c = table_chunks[0]
    assert c.sheet is not None
    assert c.row_start is not None and c.row_end is not None
    # structured rows carry canonical-able fields
    assert any("SKU" in r or "sku" in {k.lower() for k in r} for r in c.meta["rows"])


def test_xlsx_sheet_provenance(db):
    bootstrap.ensure_tenant(db, "t1", "t1")
    pid = bootstrap.ensure_project(db, tenant_id="t1", canonical_name="P").id
    data = (FIXTURES / "riyadh" / "inventory.xlsx").read_bytes()
    doc = ingest_document(db, tenant_id="t1", project_id=pid, filename="inventory.xlsx", data=data)
    db.commit()
    chunks = db.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    ).scalars().all()
    assert any(c.sheet == "Riyadh Olaya" for c in chunks)


def test_ingest_marks_document_processed(db):
    bootstrap.ensure_tenant(db, "t1", "t1")
    pid = bootstrap.ensure_project(db, tenant_id="t1", canonical_name="P").id
    doc = ingest_document(
        db, tenant_id="t1", project_id=pid, filename="n.txt", data=b"hello world",
    )
    db.commit()
    fetched = db.get(Document, doc.id)
    assert fetched.status == "processed"
    assert fetched.chunk_count >= 1
