"""custom agent on runs and quotes

Revision ID: c3e9a1f0d2b4
Revises: b7724da29c40
Create Date: 2026-10-07 18:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c3e9a1f0d2b4"
down_revision = "b7724da29c40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("agent", sa.String(length=300), nullable=True))
    with op.batch_alter_table("quotes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("agent", sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quotes", schema=None) as batch_op:
        batch_op.drop_column("agent")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("agent")
