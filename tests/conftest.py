import pytest


@pytest.fixture(autouse=True)
def benchtrace_home(tmp_path, monkeypatch):
    """Give every test its own home directory and SQLite database."""
    monkeypatch.setenv("BENCHTRACE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BENCHTRACE_DATABASE_URL", raising=False)
    yield tmp_path / "home"
