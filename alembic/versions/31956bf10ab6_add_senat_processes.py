"""add senat processes

Revision ID: 31956bf10ab6
Revises: 902b34563f20
Create Date: 2026-03-31 19:48:10.434218

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '31956bf10ab6'
down_revision: Union[str, Sequence[str], None] = '902b34563f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('senat_processes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('term', sa.Integer(), nullable=False),
    sa.Column('print_number', sa.String(length=50), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('sejm_process_number', sa.String(length=20), nullable=True),
    sa.Column('decision', sa.String(length=100), nullable=True),
    sa.Column('decision_date', sa.Date(), nullable=True),
    sa.Column('raw_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('term', 'print_number', name='uq_senat_process')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('senat_processes')
