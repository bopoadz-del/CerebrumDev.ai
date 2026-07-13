"""initial RetailOps schema (pgvector + hybrid retrieval)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-13

Creates the pgvector extension, all operational tables (single source of truth
is the ORM models), the generated tsvector column, and the vector/lexical
indexes required for hybrid retrieval.
"""

from __future__ import annotations

from alembic import op

from app.retailops.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # pgvector must exist before the embedding column is created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Create all tables from the ORM metadata (keeps DDL == models).
    Base.metadata.create_all(bind=bind)

    # Approximate-NN index for semantic search (cosine).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_embedding "
        "ON document_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    # GIN index for lexical (full-text) ranking.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_tsv "
        "ON document_chunks USING gin (text_tsv)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_chunk_tsv")
    op.execute("DROP INDEX IF EXISTS ix_chunk_embedding")
    Base.metadata.drop_all(bind=bind)
