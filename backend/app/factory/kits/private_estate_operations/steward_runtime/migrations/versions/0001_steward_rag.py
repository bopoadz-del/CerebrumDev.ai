"""initial Steward production RAG schema (pgvector + hybrid retrieval)

Revision ID: 0001_steward_rag
Revises:
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

from app.steward.models import Base

revision = "0001_steward_rag"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_steward_chunk_embedding "
        "ON document_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_steward_chunk_tsv "
        "ON document_chunks USING gin (text_tsv)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_steward_chunk_tsv")
    op.execute("DROP INDEX IF EXISTS ix_steward_chunk_embedding")
    Base.metadata.drop_all(bind=bind)
