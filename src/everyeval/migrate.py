"""Schema migrations with Alembic. Run automatically when the database is first opened."""

from __future__ import annotations

from importlib import resources

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(resources.files("everyeval") / "migrations"))
    cfg.attributes["engine"] = engine
    return cfg


def upgrade(engine: Engine) -> None:
    cfg = alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def revision(engine: Engine, message: str) -> None:
    """Developer helper: autogenerate a migration from model changes."""
    cfg = alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.revision(cfg, message=message, autogenerate=True)
