import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def benchtrace_home(tmp_path, monkeypatch):
    """Give every test its own home directory and an empty database.

    SQLite by default. Set BENCHTRACE_TEST_POSTGRES to an admin URL
    (postgresql+psycopg://user:pass@host/postgres) to run each test on a fresh Postgres database.
    """
    monkeypatch.setenv("BENCHTRACE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BENCHTRACE_DATABASE_URL", raising=False)
    monkeypatch.delenv("BENCHTRACE_AUTH", raising=False)
    admin_url = os.environ.get("BENCHTRACE_TEST_POSTGRES")
    if not admin_url:
        yield tmp_path / "home"
        return
    from sqlalchemy import create_engine, text

    name = f"bt_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    monkeypatch.setenv("BENCHTRACE_DATABASE_URL", admin_url.rsplit("/", 1)[0] + f"/{name}")
    yield tmp_path / "home"
    from benchtrace.db import _engine

    _engine.cache_clear()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()
