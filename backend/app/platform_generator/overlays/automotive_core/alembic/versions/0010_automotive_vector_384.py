"""Create chunks_v2 table for the Automotive Core RAG (384-dim BGE vectors).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11

The legacy ``chunks`` table was resized to 256 dimensions for the construction
corpus. The automotive foundation corpus uses a fresh namespace with
BAAI/bge-small-en-v1.5 at 384 dimensions so the two corpora never share a
mixed-model table.
"""

from __future__ import annotations

from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, Index, Integer, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        embedding_col = Column("embedding", Vector(384), nullable=False)
    else:
        # SQLite fallback: store float32 bytes. The vector store creates the
        # table dynamically on SQLite, but declaring it here keeps the schema
        # manifest consistent. The LargeBinary width is not enforced by SQLite.
        embedding_col = Column("embedding", LargeBinary, nullable=False)

    op.create_table(
        "chunks_v2",
        Column("chunk_id", String, primary_key=True),
        Column("project_id", String, nullable=False),
        Column("doc_id", String, nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("text", Text, nullable=False),
        embedding_col,
        Column("created_at", String, nullable=False),
        Column("embedding_model", String, nullable=False),
        Column("embedding_dim", Integer, nullable=False),
        Column("embedding_normalized", Boolean, nullable=False),
        Column("metadata", JSONB, nullable=True),
        Index("idx_chunks_v2_project", "project_id"),
        Index("idx_chunks_v2_doc", "project_id", "doc_id"),
        # Unique index enforced as a constraint for cross-database consistency.
        # Alembic/SQLAlchemy expresses this via UniqueConstraint in __table_args__.
        sqlite_autoincrement=False,
    )

    if dialect == "postgresql":
        # GENERATED ALWAYS tsvector column for BM25; the ORM does not declare it
        # because it breaks SQLite table creation, so add it in the migration.
        op.execute(
            text(
                "ALTER TABLE chunks_v2 ADD COLUMN IF NOT EXISTS "
                "text_search tsvector GENERATED ALWAYS AS "
                "(to_tsvector('english', text)) STORED"
            )
        )
        op.execute(
            text(
                "CREATE INDEX IF NOT EXISTS chunks_v2_fts_gin "
                "ON chunks_v2 USING GIN (text_search)"
            )
        )
        op.execute(
            text(
                "ALTER TABLE chunks_v2 ADD CONSTRAINT uq_chunks_v2_project_doc_index "
                "UNIQUE (project_id, doc_id, chunk_index)"
            )
        )


def downgrade() -> None:
    op.drop_table("chunks_v2")
