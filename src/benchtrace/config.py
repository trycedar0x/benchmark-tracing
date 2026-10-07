"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    home: Path
    database_url: str
    logs_dir: Path
    files_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        home = Path(os.environ.get("BENCHTRACE_HOME", Path.home() / ".benchtrace")).expanduser()
        database_url = os.environ.get("BENCHTRACE_DATABASE_URL", f"sqlite:///{home / 'benchtrace.db'}")
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
