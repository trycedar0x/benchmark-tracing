import asyncio
import contextlib
import json
import threading
from contextlib import asynccontextmanager

import pytest

from everybench.evaluation.agents import CallableAgent, HttpAgent
from everybench.evaluation.types import AgentInput, AgentInvocationError, AgentResult

SECRET = "credential-DO-NOT-DISCLOSE"
CASE = AgentInput(
    input="Compute profit.", run_id="run-1", sample_id="sample-1", attempt=2, trace_id="a" * 32, parent_span_id="b" * 16
)
CONVERSATION = {"type": "conversation", "conversationId": "c" * 43}
DELTA = {"type": "delta", "text": "4 million"}
DONE = {"type": "done"}


def ndjson(*records):
    return ("\n".join(json.dumps(record) for record in records) + "\n").encode()


@asynccontextmanager
async def local_server(handler):
    """Real loopback HTTP/1.1, including byte fragmentation and disconnects."""
    requests, tasks = [], set()

    async def connection(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode().split("\r\n")
            headers = {key.lower(): value for line in lines[1:] if line for key, value in [line.split(": ", 1)]}
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            request = {"line": lines[0], "headers": headers, "body": body}
            requests.append(request)
            await handler(request, reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests
    finally:
        server.close()
        await server.wait_closed()
        active = list(tasks)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)


def response(body, *, status="200 OK", content_type="application/x-ndjson", headers=""):
    return (
        f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n{headers}Connection: close\r\n\r\n"
    ).encode() + body


def test_callable_only_receives_agent_input_and_reports_real_identity():
    calls = []

    def target(case):
        calls.append(case)
        assert not hasattr(case, "reference")
        return "4 million"

    agent = CallableAgent(target, version="revision-7")
    assert asyncio.run(agent.invoke(CASE)).answer == "4 million"
    assert calls == [CASE]
    assert agent.describe()["identity"].endswith("target")
    assert agent.describe()["version"] == "revision-7"

    async def async_target(case):
        return AgentResult(answer=case.input)

    assert asyncio.run(CallableAgent(async_target).invoke(CASE)).answer == CASE.input


def test_callable_failure_is_safe_and_never_retried():
    calls = []

    def target(case):
        calls.append(case)
        raise RuntimeError(SECRET)

    agent = CallableAgent(target)
    with pytest.raises(AgentInvocationError) as failure:
        asyncio.run(agent.invoke(CASE))
    assert SECRET not in str(failure.value) + repr(agent) + json.dumps(agent.describe())
    assert len(calls) == 1
    with pytest.raises(AgentInvocationError):
        asyncio.run(CallableAgent(lambda case: "").invoke(CASE))


def test_callable_timeout_cancel_and_sync_thread_limitation():
    async def run():
        calls = []
        cancelled = asyncio.Event()

        async def target(case):
            calls.append(case)
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        with pytest.raises(TimeoutError):
            await CallableAgent(target, timeout=0.02).invoke(CASE)
        assert cancelled.is_set() and len(calls) == 1
        cancelled.clear()
        task = asyncio.create_task(CallableAgent(target).invoke(CASE))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and len(calls) == 2

        release, finished = threading.Event(), threading.Event()

        def sync_target(case):
            release.wait(1)
            finished.set()
            return "late answer"

        try:
            with pytest.raises(TimeoutError):
                await CallableAgent(sync_target, timeout=0.02).invoke(CASE)
            assert not finished.is_set()  # Python cannot kill this running thread.
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 1)

    asyncio.run(run())


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_http_real_financial_app_framing_fresh_history_and_headers(ending):
    async def run():
        # Matches sibling server.ts: conversationId, text deltas, bare done.
        body = ndjson(
            CONVERSATION, {"type": "delta", "text": "4 "}, {"type": "delta", "text": "million €"}, DONE
        ).replace(b"\n", ending)

        async def handler(request, reader, writer):
            wire = response(body, headers=f"x-everybench-trace-id: {CASE.trace_id}\r\n")
            for offset in range(0, len(wire), 7):
                writer.write(wire[offset : offset + 7])
                await writer.drain()
                await asyncio.sleep(0)

        async with local_server(handler) as (url, requests):
            agent = HttpAgent(url + "/api/chat", api_key=SECRET, version="app-revision")
            for _ in range(2):
                result = await agent.invoke(CASE)
                assert result.answer == "4 million €" and result.trace_id == CASE.trace_id
            assert len(requests) == 2
            for request in requests:
                assert json.loads(request["body"]) == {"message": CASE.input}
                assert request["headers"]["authorization"] == f"Bearer {SECRET}"
                assert request["headers"]["traceparent"] == CASE.traceparent
                assert request["headers"]["x-everybench-run-id"] == CASE.run_id
                assert request["headers"]["x-everybench-sample-id"] == CASE.sample_id
                assert request["headers"]["x-everybench-attempt"] == "2"
            assert SECRET not in repr(agent) + json.dumps(agent.describe())

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        ndjson(CONVERSATION, DELTA),  # partial text is not a completed answer
        ndjson(CONVERSATION, DELTA, {"type": "error", "error": SECRET}),
        ndjson(CONVERSATION, DELTA, {"type": "error", "code": "OUTPUT_TOKEN_LIMIT", "error": SECRET}),
        ndjson(CONVERSATION, DELTA, DONE, DONE),
        ndjson(CONVERSATION, DELTA, DONE, DELTA),
        ndjson(CONVERSATION, DELTA, DONE) + b"\n",
        ndjson(CONVERSATION, DELTA, DONE) + b"trailing",
        ndjson(CONVERSATION, DELTA, DONE)[:-1],
        ndjson(CONVERSATION, DONE),
        ndjson(DELTA, DONE),
        ndjson(CONVERSATION, CONVERSATION, DELTA, DONE),
        ndjson(CONVERSATION, {"type": "delta", "text": 4}, DONE),
        ndjson(CONVERSATION, DELTA, {"type": "done", "answer": "unrelated"}),
        ndjson(CONVERSATION) + b'{"type":"delta","text":"\xff"}\n' + ndjson(DONE),
        ndjson(CONVERSATION) + b'{"type":"delta","text":"a","text":"b"}\n' + ndjson(DONE),
        b"not-json\n",
    ],
)
def test_http_rejects_malformed_incomplete_and_error_streams_without_secret_leak(body):
    async def run():
        async def handler(request, reader, writer):
            writer.write(response(body))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            with pytest.raises(AgentInvocationError) as failure:
                await HttpAgent(url, api_key=SECRET).invoke(CASE)
            assert SECRET not in str(failure.value)
            assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "headers,content_type,max_bytes",
    [
        ("", "application/json", 10_000),
        ("x-everybench-trace-id: " + "d" * 32 + "\r\n", "application/x-ndjson", 10_000),
        ("x-everybench-trace-id: invalid\r\n", "application/x-ndjson", 10_000),
        ("Content-Encoding: gzip\r\n", "application/x-ndjson", 10_000),
        ("", "application/x-ndjson", 10),
    ],
)
def test_http_rejects_wrong_trace_ordinary_json_compression_and_oversize(headers, content_type, max_bytes):
    async def run():
        async def handler(request, reader, writer):
            writer.write(response(ndjson(CONVERSATION, DELTA, DONE), headers=headers, content_type=content_type))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            with pytest.raises(AgentInvocationError):
                await HttpAgent(url, max_bytes=max_bytes).invoke(CASE)
            assert len(requests) == 1

    asyncio.run(run())


def test_http_never_follows_redirect_or_forwards_credentials():
    async def run():
        async def sink(request, reader, writer):
            pytest.fail("Redirect followed")

        async with local_server(sink) as (destination, forwarded):

            async def redirect(request, reader, writer):
                writer.write(response(b"", status="307 Temporary Redirect", headers=f"Location: {destination}\r\n"))
                await writer.drain()

            async with local_server(redirect) as (url, requests):
                with pytest.raises(AgentInvocationError):
                    await HttpAgent(url, api_key=SECRET).invoke(CASE)
                assert len(requests) == 1 and forwarded == []

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_http_total_deadline_and_cancellation_close_connection(cancel):
    async def run():
        started, disconnected = asyncio.Event(), asyncio.Event()

        async def handler(request, reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nConnection: close\r\n\r\n")
            writer.write(ndjson(CONVERSATION, DELTA))
            await writer.drain()
            started.set()
            try:
                await reader.read()
            finally:
                disconnected.set()  # Cancellation may close with EOF or TCP reset.

        async with local_server(handler) as (url, requests):
            task = asyncio.create_task(HttpAgent(url, timeout=1 if cancel else 0.1).invoke(CASE))
            await asyncio.wait_for(started.wait(), 1)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await task
            await asyncio.wait_for(disconnected.wait(), 1)
            assert len(requests) == 1

    asyncio.run(run())


def test_http_token_env_and_endpoint_path_are_not_described(monkeypatch):
    monkeypatch.setenv("EVERYBENCH_TEST_AGENT_TOKEN", SECRET)
    agent = HttpAgent("https://example.test/" + SECRET, token_env="EVERYBENCH_TEST_AGENT_TOKEN")
    assert SECRET not in json.dumps(agent.describe()) + repr(agent)
    monkeypatch.delenv("EVERYBENCH_TEST_AGENT_TOKEN")
    with pytest.raises(AgentInvocationError):
        asyncio.run(agent.invoke(CASE))  # missing token fails before any network IO


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.test",
        "https://example.test/?token=secret",
        "file:///tmp/secret",
        "https://example.test/#secret",
        "http://example.test",
        "https://example.test:bad",
        "https://",
        "https://example.test/\r\nAuthorization: secret",
        "https://example.test\\@evil.test",
        "http://127.0.0.1:0",
    ],
)
def test_target_urls_fail_safely(url):
    with pytest.raises(ValueError) as failure:
        HttpAgent(url)
    assert url not in str(failure.value)


def test_http_accepts_real_chunked_ndjson_including_split_utf8():
    async def run():
        body = ndjson(CONVERSATION, {"type": "delta", "text": "€ profit"}, DONE).replace(b"\\u20ac", "€".encode())

        async def handler(request, reader, writer):
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            )
            for byte in body:
                writer.write(b"1\r\n" + bytes([byte]) + b"\r\n")
                await writer.drain()
                await asyncio.sleep(0)
            writer.write(b"0\r\n\r\n")
            await writer.drain()

        async with local_server(handler) as (url, requests):
            assert (await HttpAgent(url).invoke(CASE)).answer == "€ profit"
            assert len(requests) == 1

    asyncio.run(run())


def test_http_deadline_is_total_not_reset_by_stream_activity():
    async def run():
        sent = []

        async def handler(request, reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nConnection: close\r\n\r\n")
            writer.write(ndjson(CONVERSATION))
            await writer.drain()
            for _ in range(30):
                await asyncio.sleep(0.02)
                writer.write(ndjson(DELTA))
                await writer.drain()
                sent.append(True)
            writer.write(ndjson(DONE))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            started = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await HttpAgent(url, timeout=0.15).invoke(CASE)
            assert asyncio.get_running_loop().time() - started < 0.4
            assert len(sent) >= 2 and len(requests) == 1

    asyncio.run(run())
