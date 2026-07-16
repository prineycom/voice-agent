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

from acp_client import AcpCancelled, AcpClient, AcpConnection, AcpError, AcpToolEvent
from conftest import (
    FakeAcpProc,
    acp_agent_message,
    acp_tool_call,
    acp_tool_call_update,
)


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


# ---------------------------------------------------------------------------
# AcpClient sessions: new_session / streaming prompt / permission auto-answer.
#
# These drive the REAL client (routing + auto-answer) against the scripted
# FakeAcpProc peer, monkeypatching `create_subprocess_exec`. The prompt handle's
# `events` stream and `result` future are the surfaces the task manager consumes.
# ---------------------------------------------------------------------------


async def _started_client(fake_acp_exec, monkeypatch, proc, **client_kwargs):
    """Spawn a started AcpClient wired to ``proc`` (initialize handshake done)."""
    exec_fn, created = fake_acp_exec(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)
    client = AcpClient(**client_kwargs)
    assert await client.start() is True
    return client, created


async def _collect(handle) -> list[AcpToolEvent]:
    """Drain a prompt handle's tool-event stream to a list (terminates on close)."""
    return [event async for event in handle.events]


def _requests(proc: FakeAcpProc, method: str) -> list[dict]:
    return [r for r in proc.requests if r["method"] == method]


@pytest.mark.asyncio
async def test_new_session_returns_id_and_sends_cwd_and_mcp(fake_acp_exec, monkeypatch):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc, cwd="/work")

    sid = await client.new_session(mcp_servers=[{"name": "hermes"}])
    assert sid == "sess-1"

    new_req = _requests(proc, "session/new")[0]
    assert new_req["params"]["cwd"] == "/work"  # falls back to the client's cwd
    assert new_req["params"]["mcpServers"] == [{"name": "hermes"}]

    # An explicit cwd overrides the client default.
    await client.new_session(cwd="/elsewhere")
    assert _requests(proc, "session/new")[1]["params"]["cwd"] == "/elsewhere"

    await client.aclose()


@pytest.mark.asyncio
async def test_prompt_streams_tool_events_in_order_then_result(fake_acp_exec, monkeypatch):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    proc.script_prompt(
        session_id=sid,
        updates=[
            acp_tool_call("tc-1", "terminal: uname -a", kind="execute"),
            acp_agent_message("смотрю систему…"),
            acp_tool_call_update("tc-1", status="completed"),
        ],
        result={"stopReason": "end_turn"},  # no text → chunk fallback
    )

    handle = await client.prompt(sid, "what os")
    events = await asyncio.wait_for(_collect(handle), 1.0)
    answer = await asyncio.wait_for(handle.result, 1.0)

    # Only the two tool edges are yielded, in order; the message chunk is not.
    assert [e.kind_of_update for e in events] == ["tool_call", "tool_call_update"]
    assert events[0].tool_call_id == "tc-1"
    assert events[0].title == "terminal: uname -a"
    assert events[0].kind == "execute"
    assert events[0].status == "pending"
    assert events[1].status == "completed"
    # Result carried no text → accumulated agent_message_chunk is the answer.
    assert answer == "смотрю систему…"

    prompt_req = _requests(proc, "session/prompt")[0]
    assert prompt_req["params"]["sessionId"] == sid
    assert prompt_req["params"]["prompt"] == [{"type": "text", "text": "what os"}]

    await client.aclose()


@pytest.mark.asyncio
async def test_explicit_result_text_is_preferred_over_chunks(fake_acp_exec, monkeypatch):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    proc.script_prompt(
        session_id=sid,
        updates=[acp_agent_message("partial chunk that must be ignored")],
        result={"stopReason": "end_turn", "answer": "authoritative result"},
    )

    handle = await client.prompt(sid, "q")
    answer = await asyncio.wait_for(handle.result, 1.0)
    assert answer == "authoritative result"

    await client.aclose()


@pytest.mark.asyncio
async def test_agent_message_chunk_fallback_when_result_has_no_text(
    fake_acp_exec, monkeypatch
):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    proc.script_prompt(
        session_id=sid,
        updates=[acp_agent_message("Привет, "), acp_agent_message("это ответ.")],
        result={"stopReason": "end_turn"},  # no text field at all
    )

    handle = await client.prompt(sid, "q")
    answer = await asyncio.wait_for(handle.result, 1.0)
    assert answer == "Привет, это ответ."

    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_prompts_do_not_cross_contaminate(fake_acp_exec, monkeypatch):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid_a = await client.new_session()  # sess-1
    sid_b = await client.new_session()  # sess-2
    assert sid_a != sid_b

    # Delays force the two sessions' updates to interleave in wall-clock time.
    proc.script_prompt(
        session_id=sid_a,
        updates=[
            acp_tool_call("a-1", "terminal: A"),
            acp_tool_call_update("a-1"),
            acp_agent_message("answer A"),
        ],
        result={"stopReason": "end_turn"},
        delay=0.02,
    )
    proc.script_prompt(
        session_id=sid_b,
        updates=[
            acp_tool_call("b-1", "terminal: B"),
            acp_tool_call_update("b-1"),
            acp_agent_message("answer B"),
        ],
        result={"stopReason": "end_turn"},
        delay=0.02,
    )

    handle_a = await client.prompt(sid_a, "task A")
    handle_b = await client.prompt(sid_b, "task B")
    events_a, events_b = await asyncio.wait_for(
        asyncio.gather(_collect(handle_a), _collect(handle_b)), 2.0
    )

    # Session A's stream carries ONLY A's tool ids, and B's only B's — no leakage.
    assert {e.tool_call_id for e in events_a} == {"a-1"}
    assert {e.tool_call_id for e in events_b} == {"b-1"}
    assert await asyncio.wait_for(handle_a.result, 1.0) == "answer A"
    assert await asyncio.wait_for(handle_b.result, 1.0) == "answer B"

    await client.aclose()


@pytest.mark.asyncio
async def test_permission_request_is_auto_answered_and_prompt_completes(
    fake_acp_exec, monkeypatch
):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    proc.script_prompt(
        session_id=sid,
        server_request={
            "method": "session/request_permission",
            "params": {
                "options": [
                    {"optionId": "reject", "kind": "reject_once", "name": "No"},
                    {"optionId": "allow-1", "kind": "allow_once", "name": "Yes"},
                ]
            },
        },
        updates=[acp_agent_message("done after approval")],
        result={"stopReason": "end_turn"},
    )

    handle = await client.prompt(sid, "do risky thing")
    answer = await asyncio.wait_for(handle.result, 1.0)
    assert answer == "done after approval"  # the prompt still completed

    # Exactly one client response, granting the first allow-kind option.
    assert len(proc.client_responses) == 1
    assert proc.client_responses[0]["result"] == {
        "outcome": {"outcome": "selected", "optionId": "allow-1"}
    }

    await client.aclose()


@pytest.mark.asyncio
async def test_proc_death_mid_prompt_fails_result_and_ends_stream(
    fake_acp_exec, monkeypatch
):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    proc.script_prompt(
        session_id=sid,
        updates=[acp_tool_call("tc-1", "terminal: slow")],
        delay=0.01,
        die=True,  # crash mid-prompt: no final answer, stdout EOFs
    )

    handle = await client.prompt(sid, "long task")

    # The event stream terminates (the one streamed edge, then close — no hang)...
    events = await asyncio.wait_for(_collect(handle), 1.0)
    assert [e.tool_call_id for e in events] == ["tc-1"]

    # ...and the result future fails honestly instead of hanging forever.
    with pytest.raises(AcpError):
        await asyncio.wait_for(handle.result, 1.0)

    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_prompt_fails_result_with_cancelled_and_ends_stream(
    fake_acp_exec, monkeypatch
):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    # Slow script: the delay after each update keeps the prompt in flight long
    # enough to cancel it mid-stream.
    proc.script_prompt(
        session_id=sid,
        updates=[
            acp_tool_call("tc-1", "terminal: slow"),
            acp_tool_call_update("tc-1"),
        ],
        result={"stopReason": "end_turn"},
        delay=0.5,
    )

    handle = await client.prompt(sid, "long task")

    # Wait for the first tool event so the cancel lands mid-prompt, not before it.
    first = await asyncio.wait_for(handle.events.__anext__(), 1.0)
    assert first.tool_call_id == "tc-1"

    await client.cancel_prompt(sid)

    # The result future fails with the dedicated cancellation error...
    with pytest.raises(AcpCancelled):
        await asyncio.wait_for(handle.result, 1.0)
    # ...and the events stream terminates instead of hanging its consumer.
    rest = await asyncio.wait_for(_collect(handle), 1.0)
    assert all(isinstance(e, AcpToolEvent) for e in rest)

    # The best-effort session/cancel notification went out on the wire.
    cancels = [r for r in proc.requests if r["method"] == "session/cancel"]
    assert cancels and cancels[0]["params"]["sessionId"] == sid

    await client.aclose()


@pytest.mark.asyncio
async def test_reprompt_same_session_in_flight_raises_and_original_completes(
    fake_acp_exec, monkeypatch
):
    proc = FakeAcpProc()
    client, _ = await _started_client(fake_acp_exec, monkeypatch, proc)

    sid = await client.new_session()
    proc.script_prompt(
        session_id=sid,
        updates=[
            acp_tool_call("tc-1", "terminal: slow"),
            acp_tool_call_update("tc-1"),
            acp_agent_message("original answer"),
        ],
        result={"stopReason": "end_turn"},
        delay=0.05,
    )

    handle = await client.prompt(sid, "task one")

    # A second prompt on the SAME session while the first is in flight is
    # rejected loudly — updates route by sessionId alone, so allowing it would
    # cross-contaminate the new handle's stream with the old RPC's updates.
    with pytest.raises(AcpError, match="already in flight"):
        await client.prompt(sid, "task two")

    # The original prompt is unaffected: its events and result complete normally.
    events = await asyncio.wait_for(_collect(handle), 2.0)
    assert [e.tool_call_id for e in events] == ["tc-1", "tc-1"]
    assert await asyncio.wait_for(handle.result, 1.0) == "original answer"

    # Only ONE session/prompt ever reached the wire (the rejected one sent nothing).
    assert len(_requests(proc, "session/prompt")) == 1

    # After completion the session is free again: a new prompt is accepted.
    proc.script_prompt(session_id=sid, result={"stopReason": "end_turn", "answer": "ok"})
    handle2 = await client.prompt(sid, "task three")
    assert await asyncio.wait_for(handle2.result, 1.0) == "ok"

    await client.aclose()
