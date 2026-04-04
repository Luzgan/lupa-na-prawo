"""initial schema

Revision ID: 1c4aaae8177f
Revises:
Create Date: 2026-03-29 17:43:23.466843

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "1c4aaae8177f"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "acts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("eli_id", sa.Text(), nullable=False, unique=True),
        sa.Column("eli_address", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("act_type", sa.String(100)),
        sa.Column("status", sa.String(50)),
        sa.Column("in_force", sa.String(20)),
        sa.Column("announcement_date", sa.Date()),
        sa.Column("entry_into_force", sa.Date()),
        sa.Column("publisher", sa.String(10), server_default="DU"),
        sa.Column("year", sa.Integer()),
        sa.Column("position", sa.Integer()),
        sa.Column("keywords", sa.ARRAY(sa.Text())),
        sa.Column("raw_html_hash", sa.String(64)),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("act_id", sa.UUID(), sa.ForeignKey("acts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part_num", sa.String(20)),
        sa.Column("part_title", sa.Text()),
        sa.Column("title_num", sa.String(20)),
        sa.Column("title_name", sa.Text()),
        sa.Column("section_num", sa.String(20)),
        sa.Column("section_title", sa.Text()),
        sa.Column("chapter_num", sa.String(20)),
        sa.Column("chapter_title", sa.Text()),
        sa.Column("article_num", sa.String(20), nullable=False),
        sa.Column("paragraph_num", sa.String(20)),
        sa.Column("point_num", sa.String(20)),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("text_for_embedding", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024)),
        sa.Column("char_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_chunks_act_id", "chunks", ["act_id"])
    op.create_index("idx_chunks_article_num", "chunks", ["article_num"])
    op.execute(
        "CREATE INDEX idx_chunks_embedding ON chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "act_references",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "source_act_id", sa.UUID(), sa.ForeignKey("acts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("target_eli_id", sa.Text(), nullable=False),
        sa.Column(
            "target_act_id", sa.UUID(), sa.ForeignKey("acts.id", ondelete="SET NULL")
        ),
        sa.Column("reference_type", sa.String(100), nullable=False),
        sa.Column("effective_date", sa.Date()),
    )

    op.create_table(
        "legislative_processes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("term", sa.Integer(), nullable=False),
        sa.Column("process_number", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(100)),
        sa.Column("document_date", sa.Date()),
        sa.Column("process_start", sa.Date()),
        sa.Column("closure_date", sa.Date()),
        sa.Column("passed", sa.Boolean()),
        sa.Column("urgency_status", sa.String(50)),
        sa.Column("related_act_eli", sa.Text()),
        sa.Column("change_date", sa.DateTime(timezone=True)),
        sa.Column("raw_json", sa.dialects.postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("term", "process_number", name="uq_process_term_number"),
    )

    op.create_table(
        "votings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("term", sa.Integer(), nullable=False),
        sa.Column("sitting", sa.Integer(), nullable=False),
        sa.Column("voting_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("result", sa.String(20)),
        sa.Column("yes_count", sa.Integer()),
        sa.Column("no_count", sa.Integer()),
        sa.Column("abstain_count", sa.Integer()),
        sa.Column("raw_json", sa.dialects.postgresql.JSONB()),
        sa.Column(
            "process_id",
            sa.UUID(),
            sa.ForeignKey("legislative_processes.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("term", "sitting", "voting_number", name="uq_voting"),
    )

    op.create_table(
        "ingestion_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("ingestion_log")
    op.drop_table("votings")
    op.drop_table("legislative_processes")
    op.drop_table("act_references")
    op.drop_index("idx_chunks_embedding", "chunks")
    op.drop_index("idx_chunks_article_num", "chunks")
    op.drop_index("idx_chunks_act_id", "chunks")
    op.drop_table("chunks")
    op.drop_table("acts")
    op.execute("DROP EXTENSION IF EXISTS vector")
