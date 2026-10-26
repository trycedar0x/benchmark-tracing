"""Schema migrations with Alembic. Run automatically when the database is first opened."""

from __future__ import annotations

from importlib import resources

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(resources.files("everybench") / "migrations"))
    cfg.attributes["engine"] = engine
    return cfg


def upgrade(engine: Engine) -> None:
    cfg = alembic_config(engine)
    if engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        return
    # SQLite cannot rebuild a referenced parent table with FK actions enabled:
    # DROP TABLE would cascade-delete samples. Only this dedicated migration
    # connection disables actions, outside a transaction, and restores them even
    # on failure. An explicit BEGIN makes the DDL and FK check atomic.
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Migration violated foreign key integrity.")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def revision(engine: Engine, message: str) -> None:
    """Developer helper: autogenerate a migration from model changes."""
    cfg = alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.revision(cfg, message=message, autogenerate=True)
