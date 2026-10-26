"""Explicit, SDK-only user-agent transports. Neither transport retries invocations.

HTTP targets are operator-selected capabilities, not a safe arbitrary-target public
proxy. Callers exposing these adapters over a network need their own target policy.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from everybench.evaluation.types import AgentCallable, AgentInput, AgentInvocationError, AgentResult

_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9_-]{43}")


def _validate_url(url: str) -> str:
    """Disallow credential-bearing/ambiguous URLs; permit explicit local HTTP testing."""
    try:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in url
            or "%" in parsed.netloc
            or parsed.port == 0
        ):
            raise ValueError
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(
            "Use an explicit HTTPS URL (or loopback HTTP) without credentials, query or fragment."
        ) from None
    return url


def _endpoint_description(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    # Even an endpoint path can contain a capability token. Never copy it into reports.
    return {"origin": f"{parsed.scheme}://{parsed.netloc}", "endpoint_sha256": hashlib.sha256(url.encode()).hexdigest()}


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Timeout must be positive and finite.")
    return value


def _authorization(api_key: str | None) -> dict[str, str]:
    if api_key is None:
        return {}
    if not isinstance(api_key, str) or not api_key or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
        raise ValueError("Invalid bearer credential.")
    return {"Authorization": f"Bearer {api_key}"}


def _strict_json(text: str) -> Any:
    import json

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError("Non-finite JSON value.")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


class CallableAgent:
    """Run a sync or async callback with ONLY AgentInput, never references/rubrics.

    Cancelling or timing out a synchronous Python callback stops waiting, but cannot
    terminate its worker thread or undo side effects. Such callbacks must implement
    their own cooperative cancellation when execution must actually be stopped.
    """

    def __init__(
        self, callback: AgentCallable, *, name: str | None = None, version: str | None = None, timeout: float = 40
    ):
        if not callable(callback):
            raise TypeError("Agent callback must be callable.")
        self._callback = callback
        cls = type(callback)
        self._identity = (
            f"{getattr(callback, '__module__', cls.__module__)}.{getattr(callback, '__qualname__', cls.__qualname__)}"
        )
        self._name = name or self._identity
        self._version = version
        self._timeout = _timeout(timeout)

    def describe(self) -> dict[str, Any]:
        return {
            "type": "callable",
            "name": self._name,
            "identity": self._identity,
            "version": self._version,
            "timeout": self._timeout,
            "retries": 0,
        }

    async def invoke(self, case: AgentInput) -> AgentResult:
        try:
            async with asyncio.timeout(self._timeout):
                if inspect.iscoroutinefunction(self._callback):
                    result = self._callback(case)
                else:
                    result = await asyncio.to_thread(self._callback, case)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, str):
                    result = AgentResult(answer=result)
                if not isinstance(result, AgentResult):
                    raise ValueError
                return result
        except TimeoutError:
            raise TimeoutError("Agent invocation timed out.") from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AgentInvocationError("Agent callback failed or returned an invalid result.") from None


class HttpAgent:
    """POST strict financial-app NDJSON, requiring conversation/delta/explicit done.

    Each invocation sends only {"message": case.input}; conversation IDs are never
    reused. JSON answers and redirects are deliberately unsupported. A returned
    trace ID must equal the invocation's traceparent ID, not an untrusted alias.
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        token_env: str | None = None,
        name: str = "agent",
        version: str | None = None,
        timeout: float = 40,
        max_bytes: int = 1_048_576,
    ):
        self._url = _validate_url(url)
        if api_key is not None and token_env is not None:
            raise ValueError("Choose either api_key or token_env.")
        if token_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise ValueError("Invalid credential environment variable name.")
        _authorization(api_key)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer.")
        self._api_key, self._token_env = api_key, token_env
        self._name, self._version = name, version
        self._timeout, self._max_bytes = _timeout(timeout), max_bytes

    def describe(self) -> dict[str, Any]:
        return {
            "type": "http",
            "name": self._name,
            "version": self._version,
            **_endpoint_description(self._url),
            "timeout": self._timeout,
            "max_bytes": self._max_bytes,
            "authenticated": bool(self._api_key or self._token_env),
            "protocol": "financial-app-ndjson",
            "retries": 0,
        }

    async def invoke(self, case: AgentInput) -> AgentResult:
        try:
            key = self._api_key
            if self._token_env:
                key = os.environ.get(self._token_env)
                if not key:
                    raise ValueError
            headers = {
                **_authorization(key),
                "traceparent": case.traceparent,
                "x-everybench-run-id": case.run_id,
                "x-everybench-sample-id": case.sample_id,
                "x-everybench-attempt": str(case.attempt),
                "Accept": "application/x-ndjson",
                "Accept-Encoding": "identity",
            }
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=self._timeout) as client:
                    async with client.stream(
                        "POST", self._url, json={"message": case.input}, headers=headers
                    ) as response:
                        if response.status_code != 200:
                            raise ValueError
                        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if media not in ("application/x-ndjson", "application/ndjson"):
                            raise ValueError
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise ValueError
                        returned_id = response.headers.get("x-everybench-trace-id")
                        if returned_id is not None and (
                            not _TRACE_ID.fullmatch(returned_id)
                            or returned_id == "0" * 32
                            or returned_id != case.trace_id
                        ):
                            raise ValueError
                        buffer = b""
                        size, conversation, done = 0, False, False
                        parts: list[str] = []
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self._max_bytes:
                                raise ValueError
                            buffer += chunk
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                if done or not line:
                                    raise ValueError
                                record = _strict_json(line.decode("utf-8", errors="strict"))
                                if not isinstance(record, dict):
                                    raise ValueError
                                kind = record.get("type")
                                if kind == "conversation" and not conversation and not parts:
                                    if (
                                        set(record) != {"type", "conversationId"}
                                        or not isinstance(record["conversationId"], str)
                                        or not _CONVERSATION_ID.fullmatch(record["conversationId"])
                                    ):
                                        raise ValueError
                                    conversation = True
                                elif kind == "delta" and conversation:
                                    if set(record) != {"type", "text"} or not isinstance(record["text"], str):
                                        raise ValueError
                                    parts.append(record["text"])
                                elif kind == "done" and conversation:
                                    if set(record) != {"type"} or not "".join(parts).strip():
                                        raise ValueError
                                    done = True
                                else:
                                    raise ValueError
                        if buffer or not done:
                            raise ValueError
                        return AgentResult(answer="".join(parts), trace_id=returned_id or case.trace_id)
        except (TimeoutError, httpx.TimeoutException):
            raise TimeoutError("Agent invocation timed out.") from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AgentInvocationError("HTTP agent failed or returned invalid/incomplete NDJSON.") from None
