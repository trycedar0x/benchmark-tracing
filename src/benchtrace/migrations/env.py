"""Alembic environment: migrations run against the engine benchtrace passes in."""

from alembic import context

from benchtrace.db import Base

target_metadata = Base.metadata
connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
