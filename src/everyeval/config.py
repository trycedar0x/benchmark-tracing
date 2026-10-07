"""Runtime configuration, read from environment variables.

The project was called benchtrace before; its BENCHTRACE_* settings and ~/.benchtrace folder still work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    """Read EVERYEVAL_<name>, falling back to the old BENCHTRACE_<name>."""
    return os.environ.get(f"EVERYEVAL_{name}", os.environ.get(f"BENCHTRACE_{name}", default))


def home_dir() -> Path:
    """EVERYEVAL_HOME, or ~/.everyeval. An existing ~/.benchtrace is used until ~/.everyeval exists."""
    if configured := env("HOME"):
        return Path(configured).expanduser()
    home, legacy = Path.home() / ".everyeval", Path.home() / ".benchtrace"
    return legacy if not home.exists() and legacy.exists() else home


@dataclass(frozen=True)
class Settings:
    home: Path
    database_url: str
    logs_dir: Path
    files_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        home = home_dir()
        db_file = home / "everyeval.db"
        if not db_file.exists() and (home / "benchtrace.db").exists():
            db_file = home / "benchtrace.db"
        database_url = env("DATABASE_URL", f"sqlite:///{db_file}")
        return cls(
            home=home,
            database_url=database_url,
            logs_dir=home / "logs",
            files_dir=home / "files",
        )

    def ensure_dirs(self) -> None:
        for path in (self.home, self.logs_dir, self.files_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


def settings() -> Settings:
    return Settings.from_env()
