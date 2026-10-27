"""business notifications settings

Revision ID: 7c1e2d9a4b10
Revises: 513a81c1461a
Create Date: 2026-10-27 02:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '7c1e2d9a4b10'
down_revision = '513a81c1461a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('businesses', sa.Column('notifications', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('businesses', 'notifications')
