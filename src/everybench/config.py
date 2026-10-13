"""Runtime configuration, read from environment variables.

Legacy everyeval and benchtrace settings and data folders still work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    """Read EVERYBENCH_<name>, then legacy EVERYEVAL_<name> and BENCHTRACE_<name>."""
    for prefix in ("EVERYBENCH", "EVERYEVAL", "BENCHTRACE"):
        if (key := f"{prefix}_{name}") in os.environ:
            return os.environ[key]
    return default


def home_dir() -> Path:
    """Configured home, or ~/.everybench, falling back to existing legacy data folders."""
    if configured := env("HOME"):
        return Path(configured).expanduser()
    home = Path.home() / ".everybench"
    for folder in (".everybench", ".everyeval", ".benchtrace"):
        candidate = Path.home() / folder
        if candidate.exists():
            return candidate
    return home


@dataclass(frozen=True)
class Settings:
    home: Path
    database_url: str
    logs_dir: Path
    files_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        home = home_dir()
        db_file = home / "everybench.db"
        if not db_file.exists():
            for filename in ("everyeval.db", "benchtrace.db"):
                if (legacy := home / filename).exists():
                    db_file = legacy
                    break
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
