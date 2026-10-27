"""Operator-owned network capabilities. Never accept target configuration from callers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from everybench.config import env
from everybench.evaluation.agents import _endpoint_description, _strict_json, _validate_url


class TraceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["store", "http"]
    url: str | None = None
    token_secret: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,99}$")

    @model_validator(mode="after")
    def validate_transport(self):
        if self.type == "http":
            _validate_url(self.url)
        elif self.url is not None or self.token_secret is not None:
            raise ValueError("Store traces do not use HTTP configuration.")
        return self


class TargetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    url: str
    workspace_id: str | None  # Explicit null is local-only, never public/wildcard.
    token_secret: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,99}$")
    trace: TraceProfile | None = None
    data_path: str | None = None

    @model_validator(mode="after")
    def validate_transport(self):
        _validate_url(self.url)
        if self.data_path is not None and not Path(self.data_path).is_absolute():
            raise ValueError("Operator dataset paths must be absolute.")
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "origin": _endpoint_description(self.url)["origin"],
            "has_trace_source": self.trace is not None,
        }


def profiles(workspace_id: str | None) -> list[TargetProfile]:
    path = env("TARGETS_FILE")
    if not path:
        return []
    try:
        raw = _strict_json(Path(path).read_text())
        if not isinstance(raw, list):
            raise ValueError
        entries = [TargetProfile.model_validate(item) for item in raw]
        identities = [(p.workspace_id, p.id) for p in entries]
        if len(set(identities)) != len(identities):
            raise ValueError
    except Exception:
        raise ValueError("Operator target configuration unavailable or invalid.") from None
    return [p for p in entries if p.workspace_id == workspace_id]


def resolve_target(target_id: str, workspace_id: str | None) -> TargetProfile:
    for profile in profiles(workspace_id):
        if profile.id == target_id:
            return profile
    raise LookupError("Evaluation target not found.")


def loopback_host(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def local_request_allowed(peer: str | None, url: str, origin: str | None, fetch_site: str | None) -> bool:
    parsed = urlsplit(url)
    if not loopback_host(peer) or not loopback_host(parsed.hostname):
        return False
    if fetch_site not in (None, "none", "same-origin"):
        return False
    if origin is not None:
        try:
            source = urlsplit(origin)

            def port(u):
                return u.port or (443 if u.scheme == "https" else 80)

            if (source.scheme, source.hostname, port(source)) != (parsed.scheme, parsed.hostname, port(parsed)):
                return False
            if source.username or source.password or source.path or source.query or source.fragment:
                return False
        except ValueError:
            return False
    return True
