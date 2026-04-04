"""add sejm prints and print chunks

Revision ID: 902b34563f20
Revises: 1c4aaae8177f
Create Date: 2026-03-31 19:44:06.492066

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '902b34563f20'
down_revision: Union[str, Sequence[str], None] = '1c4aaae8177f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('sejm_prints',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('term', sa.Integer(), nullable=False),
    sa.Column('print_number', sa.String(length=50), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('document_date', sa.Date(), nullable=True),
    sa.Column('process_number', sa.String(length=20), nullable=True),
    sa.Column('attachment_url', sa.Text(), nullable=True),
    sa.Column('text_content', sa.Text(), nullable=True),
    sa.Column('text_hash', sa.String(length=64), nullable=True),
    sa.Column('raw_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('term', 'print_number', name='uq_sejm_print_term_number')
    )
    op.create_table('print_chunks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('print_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('text_content', sa.Text(), nullable=False),
    sa.Column('text_for_embedding', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['print_id'], ['sejm_prints.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_print_chunks_embedding', 'print_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('idx_print_chunks_print_id', 'print_chunks', ['print_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_print_chunks_print_id', table_name='print_chunks')
    op.drop_index('idx_print_chunks_embedding', table_name='print_chunks', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('print_chunks')
    op.drop_table('sejm_prints')
