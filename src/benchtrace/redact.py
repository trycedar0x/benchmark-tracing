"""Content policy and best-effort secret redaction.

Redaction is never complete: it catches common credential formats and
sensitive field names, not secrets in arbitrary prose.
"""

from __future__ import annotations

import re
from typing import Any

POLICIES = ("metadata", "redacted", "full")

# Matches whole key segments at the end of a key: "api_key", "github_token", "Authorization",
# but not "input_tokens" or "max_tokens".
SENSITIVE_KEYS = re.compile(
    r"(^|[_.\-])(api[_-]?key|authorization|password|passwd|secret|secret[_-]?key|token|cookie|"
    r"private[_-]?key|credentials?|set[_-]?cookie)$",
    re.I,
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
]


def redact_text(text: str) -> str:
    for name, pattern in PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


def redact(value: Any) -> Any:
    """Redact strings recursively; drop values under sensitive keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: "[REDACTED:field]" if isinstance(k, str) and SENSITIVE_KEYS.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def apply_policy(content: Any, policy: str) -> tuple[Any, str]:
    """Return (stored content, content_state) for a content policy."""
    if content is None:
        return None, "missing"
    if policy == "metadata":
        return None, "withheld"
    if policy == "redacted":
        return redact(content), "redacted"
    if policy == "full":
        return content, "present"
    raise ValueError(f"Unknown content policy {policy!r}; choose one of {POLICIES}")


def scrub_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Attributes are kept under every policy, so sensitive keys are always dropped."""
    return {
        k: "[REDACTED:field]" if SENSITIVE_KEYS.search(k) else (redact_text(v) if isinstance(v, str) else v)
        for k, v in attributes.items()
    }
