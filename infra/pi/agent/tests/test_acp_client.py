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

from acp_client import AcpClient, AcpConnection, AcpError
from conftest import FakeAcpProc


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


# ---------------------------------------------------------------------------
# AcpClient supervisor: spawn + initialize + crash detection + respawn.
#
# These drive the REAL supervisor against the scripted FakeAcpProc peer from
# conftest, monkeypatching `asyncio.create_subprocess_exec` so no real `hermes`
# process is spawned. See docs/adr/0022-hermes-acp-hybrid-delegation.md.
# ---------------------------------------------------------------------------


class _HangingInitProc(FakeAcpProc):
    """A FakeAcpProc that NEVER answers ``initialize`` — models a cold-start hang.

    Everything else behaves like the base fake; only ``initialize`` is swallowed so
    the client's handshake ``wait_for`` must time out.
    """

    async def _handle(self, msg: dict) -> None:
        if msg.get("method") == "initialize":
            return  # never respond — the client's initialize wait_for must fire
        await super()._handle(msg)


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    """Poll ``predicate`` until it is truthy or ``timeout`` elapses."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate(), "condition not met within timeout"


@pytest.mark.asyncio
async def test_start_sends_initialize_and_is_available(fake_acp_exec, monkeypatch):
    proc = FakeAcpProc()
    exec_fn, created = fake_acp_exec(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)

    client = AcpClient()
    assert await client.start() is True
    assert client.available is True

    # Spawned exactly `hermes acp --accept-hooks`, and initialize was the first
    # request with protocolVersion 1.
    assert proc.argv == ["hermes", "acp", "--accept-hooks"]
    assert proc.requests[0]["method"] == "initialize"
    assert proc.requests[0]["params"] == {"protocolVersion": 1}

    await client.aclose()


@pytest.mark.asyncio
async def test_start_missing_binary_returns_false_without_raising(monkeypatch):
    async def _raise(*_args, **_kwargs):
        raise FileNotFoundError("hermes: not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

    client = AcpClient()
    # Must not raise — Hermes-down is never startup-fatal.
    assert await client.start() is False
    assert client.available is False

    await client.aclose()  # idempotent no-op when nothing spawned


@pytest.mark.asyncio
async def test_start_initialize_timeout_returns_false(fake_acp_exec, monkeypatch):
    proc = _HangingInitProc()
    exec_fn, _created = fake_acp_exec(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)

    # Tiny timeout: initialize is never answered, so the handshake must give up.
    client = AcpClient(init_timeout_s=0.05)
    assert await client.start() is False
    assert client.available is False
    # The dead process was torn down.
    assert proc.returncode is not None

    await client.aclose()


@pytest.mark.asyncio
async def test_crash_flips_available_fails_pending_then_ensure_respawns(
    fake_acp_exec, monkeypatch
):
    proc1 = FakeAcpProc(pid=111)
    proc2 = FakeAcpProc(pid=222)
    exec_fn, created = fake_acp_exec(proc1, proc2)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)

    client = AcpClient()
    assert await client.start() is True

    # An in-flight request over the live connection...
    conn = await client._ensure()
    assert conn is client._conn
    pending = asyncio.ensure_future(conn.request("session/new", {"cwd": "/x"}))
    await asyncio.sleep(0)  # let it register before the crash

    # ...must fail honestly when the process dies, and `available` flips False.
    proc1.kill()
    with pytest.raises(AcpError):
        await asyncio.wait_for(pending, 1.0)
    await _wait_until(lambda: client.available is False)

    # Next _ensure() respawns a fresh process and re-runs initialize.
    conn2 = await client._ensure()
    assert client.available is True
    assert created[-1] is proc2  # the second FakeAcpProc from the factory
    assert conn2 is not conn
    assert proc2.requests[0]["method"] == "initialize"
    assert proc2.requests[0]["params"] == {"protocolVersion": 1}

    await client.aclose()


@pytest.mark.asyncio
async def test_aclose_terminates_the_process(fake_acp_exec, monkeypatch):
    proc = FakeAcpProc()
    exec_fn, _created = fake_acp_exec(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)

    client = AcpClient()
    assert await client.start() is True

    await client.aclose()
    assert proc.terminated is True
    assert client.available is False
