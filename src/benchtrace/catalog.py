"""Benchmark catalog: versioned benchmark variants and their identity."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class CatalogError(LookupError):
    pass


class TaskPatch(BaseModel):
    """A text replacement applied to a downloaded Harbor dataset, e.g. to pin a dependency's version."""

    files: str  # glob relative to each task directory
    old: str
    new: str


class BenchmarkEntry(BaseModel):
    id: str
    version: int
    family: str
    variant: str
    adapter: str = "inspect"
    task: str
    task_args: dict[str, Any] = Field(default_factory=dict)
    grader: str
    task_count: int | None = None
    license: str | None = None
    dataset: str | None = None
    requires: list[str] = Field(default_factory=lambda: ["chat"])
    sandbox: str | None = None
    offline: bool = False
    agent: str | None = None  # default Harbor agent
    task_filter: list[str] = Field(default_factory=list)  # Harbor task-name globs selecting a subset
    env: dict[str, str] = Field(default_factory=dict)  # pinned environment, e.g. a simulated user's model
    env_defaults: dict[str, str] = Field(default_factory=dict)  # used only when unset; not part of the identity
    patches: list[TaskPatch] = Field(default_factory=list)  # applied to a local copy of a Harbor dataset
    custom_agents: bool = False  # accepts `benchtrace run --agent <import path>`
    description: str | None = None
    notes: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def variant_key(self) -> str:
        """Hash of everything that defines what gets measured.

        Two runs are directly comparable only if their variant keys match.
        """
        identity = {
            "id": self.id,
            "version": self.version,
            "adapter": self.adapter,
            "task": self.task,
            "task_args": self.task_args,
            "grader": self.grader,
        }
        # Added only when set, so entries without them keep their existing keys.
        if self.task_filter:
            identity["task_filter"] = self.task_filter
        if self.env:
            identity["env"] = self.env
        if self.patches:
            identity["patches"] = [patch.model_dump() for patch in self.patches]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]


def _catalog_paths() -> list[Path]:
    paths = [Path(str(resources.files("benchtrace") / "catalog.yaml"))]
    extra = os.environ.get("BENCHTRACE_CATALOG")
    if extra:
        paths.extend(Path(p) for p in extra.split(os.pathsep) if p)
    return paths


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, BenchmarkEntry]:
    entries: dict[str, BenchmarkEntry] = {}
    for path in _catalog_paths():
        for raw in yaml.safe_load(path.read_text()) or []:
            entry = BenchmarkEntry.model_validate(raw)
            entries[entry.ref] = entry
    return entries


def get_benchmark(ref: str) -> BenchmarkEntry:
    """Resolve `id@version`, or `id` to its latest version."""
    catalog = load_catalog()
    if "@" in ref:
        if ref in catalog:
            return catalog[ref]
        raise CatalogError(f"Unknown benchmark {ref!r}. Run `benchtrace catalog` to list benchmarks.")
    versions = [e for e in catalog.values() if e.id == ref]
    if not versions:
        raise CatalogError(f"Unknown benchmark {ref!r}. Run `benchtrace catalog` to list benchmarks.")
    return max(versions, key=lambda e: e.version)
