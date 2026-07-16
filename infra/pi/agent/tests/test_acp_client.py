"""Behavioral tests for the ACP ndjson JSON-RPC transport codec.

The codec is exercised over an in-memory stream pair: a real
``asyncio.StreamReader`` (fed scripted peer lines) and a minimal fake writer that
captures the ndjson the codec emits. This keeps every test at the wire level —
we script the peer's bytes and assert on the codec's observable behavior, not on
mocks of its internals.
"""

import asyncio
import json

import pytest

from acp_client import AcpConnection, AcpError


class FakeWriter:
    """Captures ndjson written by the codec; drain/close are no-ops we record."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def messages(self) -> list[dict]:
        text = b"".join(self.chunks).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _feed(reader: asyncio.StreamReader, obj: dict) -> None:
    reader.feed_data((json.dumps(obj) + "\n").encode("utf-8"))


async def _wait(event: asyncio.Event, timeout: float = 1.0) -> None:
    await asyncio.wait_for(event.wait(), timeout)


@pytest.mark.asyncio
async def test_request_resolves_with_result():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = AcpConnection(reader, writer)

    task = asyncio.ensure_future(conn.request("initialize", {"protocolVersion": 1}))
    await asyncio.sleep(0)  # let request write and register the future

    sent = writer.messages()[-1]
    assert sent["method"] == "initialize"
    assert sent["params"] == {"protocolVersion": 1}
    assert sent["jsonrpc"] == "2.0"
    req_id = sent["id"]

    _feed(reader, {"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}})
    result = await asyncio.wait_for(task, 1.0)
    assert result == {"ok": True}

    await conn.aclose()


@pytest.mark.asyncio
async def test_error_response_raises_acp_error():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = AcpConnection(reader, writer)

    task = asyncio.ensure_future(conn.request("session/prompt", {}))
    await asyncio.sleep(0)
    req_id = writer.messages()[-1]["id"]

    err = {"code": -32601, "message": "method not found"}
    _feed(reader, {"jsonrpc": "2.0", "id": req_id, "error": err})

    with pytest.raises(AcpError) as excinfo:
        await asyncio.wait_for(task, 1.0)
    assert excinfo.value.error == err
    assert "method not found" in str(excinfo.value)

    await conn.aclose()


@pytest.mark.asyncio
async def test_notification_dispatches_callback():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    received: list[tuple[str, dict]] = []
    seen = asyncio.Event()

    def on_notification(method: str, params: dict) -> None:
        received.append((method, params))
        seen.set()

    conn = AcpConnection(reader, writer, on_notification=on_notification)

    _feed(reader, {"jsonrpc": "2.0", "method": "session/update", "params": {"x": 1}})
    await _wait(seen)
    assert received == [("session/update", {"x": 1})]

    await conn.aclose()


@pytest.mark.asyncio
async def test_server_request_dispatches_and_respond_writes_response():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = AcpConnection(reader, writer)

    got: dict = {}
    handled = asyncio.Event()

    async def on_server_request(method: str, params: dict, request_id: int) -> None:
        got["method"] = method
        got["request_id"] = request_id
        await conn.respond(request_id, {"outcome": "allow"})
        handled.set()

    conn.on_server_request = on_server_request

    _feed(
        reader,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/request_permission",
            "params": {"toolCall": "tc-1"},
        },
    )
    await _wait(handled)

    assert got["method"] == "session/request_permission"
    assert got["request_id"] == 7
    resp = writer.messages()[-1]
    assert resp == {"jsonrpc": "2.0", "id": 7, "result": {"outcome": "allow"}}

    await conn.aclose()


@pytest.mark.asyncio
async def test_aclose_fails_pending_requests():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = AcpConnection(reader, writer)

    task = asyncio.ensure_future(conn.request("session/prompt", {}))
    await asyncio.sleep(0)  # ensure the future is registered before closing

    await conn.aclose()

    with pytest.raises(AcpError):
        await asyncio.wait_for(task, 1.0)
    assert writer.closed is True


@pytest.mark.asyncio
async def test_eof_then_aclose_still_closes_writer_and_fails_pending():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = AcpConnection(reader, writer)

    task = asyncio.ensure_future(conn.request("session/prompt", {}))
    await asyncio.sleep(0)  # let the request register its pending future

    # Peer closes the stream (EOF): the reader loop exits on its own and fails
    # the pending future before anyone calls aclose.
    reader.feed_eof()
    with pytest.raises(AcpError):
        await asyncio.wait_for(task, 1.0)

    # aclose AFTER the loop already exited must still release the writer.
    await conn.aclose()
    assert writer.closed is True

    # And stay idempotent on a second call.
    await conn.aclose()


@pytest.mark.asyncio
async def test_garbage_line_does_not_kill_reader_loop():
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = AcpConnection(reader, writer)

    # Unparseable line first — the loop must log-and-continue, not die.
    reader.feed_data(b"not json at all\n")

    task = asyncio.ensure_future(conn.request("initialize", {}))
    await asyncio.sleep(0)
    req_id = writer.messages()[-1]["id"]

    # A subsequent valid response must still resolve, proving the loop survived.
    _feed(reader, {"jsonrpc": "2.0", "id": req_id, "result": {"ok": 1}})
    result = await asyncio.wait_for(task, 1.0)
    assert result == {"ok": 1}

    await conn.aclose()
