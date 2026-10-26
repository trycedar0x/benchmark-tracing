"""External agent identity independent of nullable legacy model identity."""

import sqlalchemy as sa
from alembic import op

revision = "d8f2a4c6e901"
down_revision = "c3e9a1f0d2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite requires a table rebuild for nullability. migrate.upgrade temporarily
    # disables FK actions on its dedicated connection and checks all FKs afterward.
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("target_kind", sa.String(30), nullable=False, server_default="model"))
        batch.add_column(sa.Column("target", sa.JSON(), nullable=False, server_default="{}"))
        batch.alter_column("model", existing_type=sa.String(200), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM runs WHERE model IS NULL")):
        raise RuntimeError("Cannot downgrade while external-agent runs have no model identity.")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("target")
        batch.drop_column("target_kind")
        batch.alter_column("model", existing_type=sa.String(200), nullable=False)
