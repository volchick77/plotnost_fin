"""add breakeven and status to trades

Revision ID: caa5349e9aca
Revises: 001
Create Date: 2025-11-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'caa5349e9aca'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add breakeven_moved column
    op.add_column('trades', sa.Column('breakeven_moved', sa.Boolean(), nullable=False, server_default='false'))

    # Add status column
    op.add_column('trades', sa.Column('status', sa.String(20), nullable=False, server_default='open'))

    # Create index for fast open position queries
    op.create_index('idx_trades_status', 'trades', ['status'], postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_index('idx_trades_status', table_name='trades')
    op.drop_column('trades', 'status')
    op.drop_column('trades', 'breakeven_moved')
