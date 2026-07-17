"""Test fixtures for the Agent Worker TTS plugin and health gate.

Puts the service dir on sys.path (so `import tts_plugin`/`import health`/
`import config` resolve regardless of cwd) and provides real fake servers
(no GPU, no SFU) over real sockets:

- a fake `/tts` WebSocket server (`websockets.serve` on an ephemeral port) that
  streams a configurable sequence of PCM16 chunks then `{"done": true}`, with
  variants for odd-length chunks, an `{"error": ...}` frame, and a mid-stream
  disconnect; it records the request JSON each client sends.
- a fake `/stt` WebSocket server (`websockets.serve` on an ephemeral port) that
  buffers binary PCM16 frames and, on an `{"event": "end"}` control frame,
  emits a canned transcript (optionally preceded by interim partials), with
  variants for an `{"error": ...}` frame and a mid-turn disconnect; it records
  received byte counts, raw PCM, control events, and a connection counter.
- a fake `/health` HTTP server (`aiohttp.web`) with ok (200) and degraded (503)
  routes; connection-refused is exercised by pointing at an unused port.
"""

import asyncio
import contextlib
import json
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest
import pytest_asyncio
import websockets
from aiohttp import web

SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


class FakeTTSServer:
    """A real WebSocket server mimicking the Desktop /tts contract.

    Two connection shapes, selected by ``loop``:

    - One-shot (``loop=False``, the one-shot ``ChunkedStream`` path): each
      connection reads ONE JSON request, streams its PCM, terminates, and then
      holds the socket open so the client controls teardown.
    - Streaming (``loop=True``, the persistent ``DesktopSynthesizeStream`` path):
      ONE connection serves MANY requests — it loops ``async for msg in ws``,
      and for each text message streams that message's PCM then sends a terminal
      frame, so a whole turn's sentences pipeline over a single socket.

    The request JSON each client sends is recorded in ``received`` (one entry per
    message, across both shapes). ``chunks`` is either a flat ``list[bytes]``
    (sent for every request) or a callable ``index -> list[bytes]`` so streaming
    tests can emit distinct, recoverable PCM per sentence. The terminal frame
    after each request's PCM follows ``mode``:
        - "done"       → JSON {"done": true}
        - "error"      → JSON {"error": <error>}
        - "disconnect" → close the socket mid-stream (no terminal frame)
        - "hang"       → (loop only) stream the FIRST turn's PCM but never send
                         its terminal frame, idling until the client drops the
                         socket; models a synthesis still in progress when a
                         barge-in cancels it. Reconnected turns complete normally,
                         so a dropped-then-reconnected stream can be exercised on
                         a single server.

    ``blendshapes`` models the interleaved A2F frame stream: ``None`` (default) →
    no blendshape frames; a flat ``list[dict]`` or a callable ``index ->
    list[dict]`` → those A2F frames are emitted per request AFTER the PCM and
    BEFORE the audio {"done": true}, matching production ordering so re-basing can
    be exercised.

    ``a2f`` controls the per-sentence {"type": "a2f_done"} marker sent AFTER the
    audio {"done": true} (production sends exactly one per sentence, always). It is
    decoupled from ``blendshapes`` since a sentence may carry zero frames yet still
    close its A2F stream. ``None`` (default) auto-selects: ON for the streaming
    (``loop``) shape — so the plugin's a2f-aware drain completes — and OFF for the
    one-shot shape (the ChunkedStream never reads it). Pass ``a2f=False`` to model
    a total A2F outage (no marker at all).

    ``a2f_reorder`` (``loop`` only, exactly two sentences) models an out-of-order
    background A2F fork: sentence 0's ``a2f_done`` is DEFERRED past sentence 1's
    audio ``done`` and emitted just before sentence 1's blendshape frames, so the
    plugin must pair each ``a2f_done`` with its own sentence's duration (not the
    last-finalized one) to keep reply-relative ``t`` correct.
    """

    def __init__(
        self,
        chunks,
        *,
        mode="done",
        error="boom",
        loop=False,
        blendshapes=None,
        a2f=None,
        a2f_reorder=False,
    ):

        self.chunks = chunks
        self.mode = mode
        self.error = error
        self.loop = loop
        self.blendshapes = blendshapes
        self.a2f = a2f
        self.a2f_reorder = a2f_reorder
        self.received = []  # request JSON dicts, one per received message
        self.connections = 0  # cumulative count of accepted connections
        self.active = 0  # currently-open connections (drains to 0 on no leak)
        # Set to True and signalled when the connection ends (either side closes;
        # in practice a mid-stream client cancel reaches this via ConnectionClosed
        # before the normal `done`/`error` paths do). Barge-in abort tests await
        # disconnected_event to confirm the socket closed.
        self.disconnected = False
        self.disconnected_event = asyncio.Event()
        self._server = None
        self.url = None

    def _chunks_for(self, index):
        """PCM chunks for the ``index``-th request (callable or flat list)."""
        return self.chunks(index) if callable(self.chunks) else self.chunks

    def _blendshapes_for(self, index):
        """A2F frames for the ``index``-th request (callable, flat list, or None).

        ``None`` → no blendshape frames for this request. Otherwise the frames are
        emitted before the audio ``done``.
        """
        if self.blendshapes is None:
            return None
        return (
            self.blendshapes(index)
            if callable(self.blendshapes)
            else self.blendshapes
        )

    def _emit_a2f(self, default):
        """Whether to send the ``a2f_done`` marker (auto when ``a2f`` is None)."""
        return default if self.a2f is None else self.a2f

    async def _send_a2f_and_done(self, ws, frames, send_a2f):
        """Emit [A2F frames...] {"done": true} [{"a2f_done"}] for one request.

        Frames (if any) precede the audio ``done``; the ``a2f_done`` marker follows
        it when ``send_a2f`` is set.
        """
        for frame in frames or ():
            await ws.send(json.dumps(frame))
        await ws.send(json.dumps({"done": True}))
        if send_a2f:
            await ws.send(json.dumps({"type": "a2f_done"}))

    async def _handler(self, ws):
        self.connections += 1
        self.active += 1
        try:
            if self.loop:
                await self._serve_loop(ws)
            else:
                await self._serve_once(ws)
        except websockets.exceptions.ConnectionClosed:
            # Client closed mid-stream (e.g. barge-in abort).
            pass
        finally:
            # This socket is now fully torn down; drop it from the active gauge.
            self.active -= 1
            # Always mark the disconnect so tests can assert the socket closed.
            self.disconnected = True
            self.disconnected_event.set()

    async def _serve_once(self, ws):
        """One request per connection (the one-shot ChunkedStream contract)."""
        req = await ws.recv()
        self.received.append(json.loads(req))
        for chunk in self._chunks_for(0):
            await ws.send(chunk)
        if self.mode == "error":
            await ws.send(json.dumps({"error": self.error}))
        elif self.mode == "disconnect":
            await ws.close()
            return
        else:
            await self._send_a2f_and_done(
                ws, self._blendshapes_for(0), self._emit_a2f(False)
            )
        # Keep the handler alive so the client controls teardown; this lets
        # the test assert per-utterance client-side disconnect.
        try:
            await ws.wait_closed()
        except Exception:
            pass

    async def _serve_loop_reorder(self, ws):
        """Two-sentence loop that emits sentence 0's ``a2f_done`` OUT OF ORDER.

        Wire order (models a background A2F fork finishing late):
            S0: PCM, S0 frames, audio done            (S0 a2f_done DEFERRED)
            S1: PCM, audio done, [S0 a2f_done], S1 frames, S1 a2f_done
        So sentence 0's ``a2f_done`` lands AFTER sentence 1's audio ``done`` and
        just before sentence 1's frames — the interleaving the FIFO pairing fixes.
        """
        index = 0
        async for msg in ws:
            self.received.append(json.loads(msg))
            for chunk in self._chunks_for(index):
                await ws.send(chunk)
            frames = self._blendshapes_for(index)
            if index == 0:
                for frame in frames or ():
                    await ws.send(json.dumps(frame))
                await ws.send(json.dumps({"done": True}))
                # DEFER sentence 0's a2f_done (emitted during sentence 1 below).
            else:
                await ws.send(json.dumps({"done": True}))
                # Sentence 0's deferred marker arrives now — after S1's audio done.
                await ws.send(json.dumps({"type": "a2f_done"}))
                for frame in frames or ():
                    await ws.send(json.dumps(frame))
                await ws.send(json.dumps({"type": "a2f_done"}))
            index += 1

    async def _serve_loop(self, ws):
        """Many requests over one persistent socket (the streaming contract).

        Mirrors the Desktop /tts per-message contract: read each text message,
        stream that message's PCM, then send one terminal frame for it.
        """
        if self.a2f_reorder:
            await self._serve_loop_reorder(ws)
            return
        index = 0
        async for msg in ws:
            self.received.append(json.loads(msg))
            for chunk in self._chunks_for(index):
                await ws.send(chunk)
            frames = self._blendshapes_for(index)
            index += 1
            if self.mode == "hang" and self.connections == 1:
                # First turn is left in progress: stream any configured A2F frames
                # (so a barge-in can interrupt mid-forwarding) but NO terminal
                # frame, idling until the client drops the socket (barge-in).
                # Reconnected turns (connections > 1) fall through to "done".
                for frame in frames or ():
                    await ws.send(json.dumps(frame))
                await ws.wait_closed()
                return
            if self.mode == "error":
                await ws.send(json.dumps({"error": self.error}))
            elif self.mode == "disconnect":
                await ws.close()
                return
            else:
                await self._send_a2f_and_done(ws, frames, self._emit_a2f(True))

    async def start(self):
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/tts"

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()


@pytest_asyncio.fixture
async def tts_server_factory():
    """Yield a factory that starts FakeTTSServers and tears them all down."""
    servers = []

    async def _make(
        chunks,
        *,
        mode="done",
        error="boom",
        loop=False,
        blendshapes=None,
        a2f=None,
        a2f_reorder=False,
    ):
        srv = FakeTTSServer(
            chunks,
            mode=mode,
            error=error,
            loop=loop,
            blendshapes=blendshapes,
            a2f=a2f,
            a2f_reorder=a2f_reorder,
        )
        await srv.start()
        servers.append(srv)
        return srv

    yield _make

    for srv in servers:
        await srv.stop()


class FakeSTTServer:
    """A real WebSocket server mimicking the Desktop /stt contract.

    A single long-lived connection is expected to carry many turns. Per turn
    the client streams binary PCM16 frames and then a control event:
        - {"event": "end"}   → emit transcript result(s), then clear the buffer
        - {"event": "reset"} → clear the buffer, emit nothing

    On an ``end`` event the terminal frame depends on ``mode``:
        - "final"      → JSON {"text": <transcript>, "is_final": true,
                         "partial": ""}; if ``partials`` is set, interim
                         {"text": ..., "is_final": false, "partial": ...}
                         frames are emitted first
        - "error"      → JSON {"error": <error>}
        - "disconnect" → close the socket mid-turn (no terminal frame)

    Assertion attributes:
        - ``connections``    incremented once per accepted socket (assert it
                             stays at 1 across turns on a reused connection)
        - ``received_bytes`` total count of binary PCM16 bytes received
        - ``received_pcm``   list of the raw binary frames received
        - ``events``         list of parsed control-event dicts received
    """

    def __init__(
        self,
        *,
        transcript="привет мир",
        mode="final",
        error="boom",
        partials=None,
    ):
        self.transcript = transcript
        self.mode = mode
        self.error = error
        self.partials = list(partials) if partials else []
        self.connections = 0
        self.received_bytes = 0
        self.received_pcm = []  # raw binary frames, in arrival order
        self.events = []  # parsed control-event dicts, in arrival order
        self._server = None
        self.url = None

    async def _handler(self, ws):
        self.connections += 1
        buffer = bytearray()
        try:
            async for msg in ws:
                if isinstance(msg, (bytes, bytearray)):
                    buffer.extend(msg)
                    self.received_bytes += len(msg)
                    self.received_pcm.append(bytes(msg))
                    continue
                event = json.loads(msg)
                self.events.append(event)
                if event.get("event") == "end":
                    if self.mode == "disconnect":
                        await ws.close()
                        return
                    if self.mode == "error":
                        await ws.send(json.dumps({"error": self.error}))
                    else:
                        for partial in self.partials:
                            await ws.send(
                                json.dumps(
                                    {
                                        "text": partial,
                                        "is_final": False,
                                        "partial": partial,
                                    }
                                )
                            )
                        await ws.send(
                            json.dumps(
                                {
                                    "text": self.transcript,
                                    "is_final": True,
                                    "partial": "",
                                }
                            )
                        )
                    buffer = bytearray()
                elif event.get("event") == "reset":
                    buffer = bytearray()
        except websockets.exceptions.ConnectionClosed:
            pass

    async def start(self):
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/stt"

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()


@pytest_asyncio.fixture
async def stt_server_factory():
    """Yield a factory that starts FakeSTTServers and tears them all down."""
    servers = []

    async def _make(*, transcript="привет мир", mode="final", error="boom", partials=None):
        srv = FakeSTTServer(
            transcript=transcript, mode=mode, error=error, partials=partials
        )
        await srv.start()
        servers.append(srv)
        return srv

    yield _make

    for srv in servers:
        await srv.stop()


@pytest_asyncio.fixture
async def health_server():
    """Fake /health HTTP server with ok (200) and degraded (503) routes.

    Exposes ``base_url``; ``ok_url`` and ``degraded_url`` are convenience props.
    """

    async def ok(_request):
        return web.json_response(
            {"status": "ok", "service": "tts", "model_loaded": True}, status=200
        )

    async def degraded(_request):
        return web.json_response(
            {"status": "degraded", "service": "tts", "model_loaded": False},
            status=503,
        )

    app = web.Application()
    app.router.add_get("/health", ok)
    app.router.add_get("/health-degraded", degraded)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    class _Health:
        base_url = f"http://127.0.0.1:{port}"
        ok_url = f"http://127.0.0.1:{port}/health"
        degraded_url = f"http://127.0.0.1:{port}/health-degraded"

    yield _Health()

    await runner.cleanup()


# ---------------------------------------------------------------------------
# Scripted fake `hermes acp` subprocess harness (real transport, scripted peer)
#
# `FakeAcpProc` looks like an ``asyncio.subprocess.Process`` to the code under
# test (``AcpClient`` supervisor and the rewritten ``HermesTaskManager``) but is
# driven entirely in-process: its ``stdin`` is a sink that parses the ndjson
# JSON-RPC the client writes, and its ``stdout`` is a real
# ``asyncio.StreamReader`` the fake feeds scripted response/notification lines
# into. Behaviour is scripted per-method (``initialize`` / ``session/new``
# defaults; ``session/prompt`` via :meth:`FakeAcpProc.script_prompt`), so tests
# exercise the *real* ACP codec against a *scripted* peer while *recording* every
# message the client sends. See docs/adr/0022-hermes-acp-hybrid-delegation.md.
# ---------------------------------------------------------------------------


def acp_tool_call(tool_id, title, *, kind="execute", status="pending"):
    """Build ``session/update`` params for a tool-call START edge (ADR-0022).

    Shape: ``{"update": {"sessionUpdate": "tool_call", "toolCallId": <id>,
    "title": <"terminal: uname -a">, "kind": <"execute">, "status": <"pending">}}``.
    The tool name lives in ``title`` (humanised by the manager's template map),
    ``kind`` is the ACP tool category and ``status`` its lifecycle state. The
    fake injects ``sessionId`` into the params when it emits the notification.
    """
    return {
        "update": {
            "sessionUpdate": "tool_call",
            "toolCallId": tool_id,
            "title": title,
            "kind": kind,
            "status": status,
        }
    }


def acp_tool_call_update(tool_id, *, status="completed"):
    """Build ``session/update`` params for a tool-call FINISH edge (ADR-0022).

    Shape: ``{"update": {"sessionUpdate": "tool_call_update", "toolCallId":
    <id>, "status": <"completed">}}`` — the closing edge correlated to the START
    edge by ``toolCallId``.
    """
    return {
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_id,
            "status": status,
        }
    }


def acp_agent_message(text):
    """Build ``session/update`` params for an assistant text chunk.

    Shape: ``{"update": {"sessionUpdate": "agent_message_chunk", "content":
    {"type": "text", "text": <text>}}}``.
    """
    return {
        "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        }
    }


@dataclass
class _PromptScript:
    """One scripted answer for a ``session/prompt`` request, consumed FIFO.

    ``updates`` are ``session/update`` params dicts (see the ``acp_*`` builders)
    emitted as notifications in order, optionally spaced by ``delay`` seconds.
    ``server_request`` (``{"method", "params"}``, default
    ``session/request_permission``) is emitted as a server→client request *before*
    the updates and its response awaited/recorded. Then the prompt is answered:
    ``error`` (a JSON-RPC error object) → error response; else ``result`` →
    result response (defaults to ``{"stopReason": "end_turn"}``; script e.g.
    ``result={"stopReason": "cancelled"}`` for a cancellation). ``die=True`` skips
    the answer entirely and crashes the process mid-prompt (stdout EOF), so the
    client's pending request fails — the crash-mid-task path.
    """

    updates: list = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[dict] = None
    delay: float = 0.0
    server_request: Optional[dict] = None
    die: bool = False


class _FakeStdin:
    """An ``asyncio.StreamWriter``-like sink feeding the fake's request reader.

    The client writes ndjson JSON-RPC here; every write is forwarded verbatim to
    an internal ``StreamReader`` the servicing task reads line-by-line. Only the
    surface the ACP codec / subprocess supervisor touches is implemented
    (``write`` / ``drain`` / ``write_eof`` / ``close`` / ``is_closing`` /
    ``wait_closed``). ``close`` feeds EOF so the servicer's ``readline`` returns
    and the peer shuts down cleanly.
    """

    def __init__(self, target: asyncio.StreamReader) -> None:
        self._target = target
        self._closing = False

    def write(self, data: bytes) -> None:
        if self._closing:
            return
        self._target.feed_data(bytes(data))

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        self.close()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        with contextlib.suppress(Exception):
            self._target.feed_eof()

    def is_closing(self) -> bool:
        return self._closing

    async def wait_closed(self) -> None:
        return None


class FakeAcpProc:
    """A scripted, long-lived ndjson JSON-RPC peer that quacks like a Process.

    Mirrors the spirit of ``FakeProc`` in ``test_hermes_tasks.py`` but for the
    persistent ``hermes acp`` transport rather than a one-shot ``communicate()``.
    It exposes the ``asyncio.subprocess.Process`` surface the code under test
    uses — ``stdin`` (writer-like), ``stdout`` / ``stderr``
    (``asyncio.StreamReader``), ``pid``, ``returncode``, and ``wait()`` /
    ``terminate()`` / ``kill()`` — and runs an internal servicing task that
    parses each inbound line as a JSON-RPC request and answers it.

    Defaults: ``initialize`` → ``{"protocolVersion": 1}`` (overridable via
    ``initialize_result``); ``session/new`` → ``{"sessionId": "sess-<n>"}``
    (unique per call). ``session/prompt`` is answered from scripts registered
    with :meth:`script_prompt`, matched per ``sessionId`` first then from a
    session-agnostic FIFO fallback; an unscripted prompt gets a bare
    ``{"stopReason": "end_turn"}``.

    Recording: every inbound request/notification is appended to ``requests``;
    every client response to a server→client request is appended to
    ``client_responses``.

    Crash modelling: ``kill()`` / ``terminate()`` (or a ``die`` script) set
    ``returncode``, EOF ``stdout`` and unblock ``wait()`` — so a supervisor's
    crash/respawn path can be exercised.
    """

    def __init__(self, *, initialize_result: Optional[dict] = None, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: Optional[int] = None
        self.requests: list[dict] = []  # every inbound request/notification, in order
        self.client_responses: list[dict] = []  # responses to server→client requests
        self.terminated = False
        self.killed = False
        self.argv: Optional[list] = None  # set by the fake_acp_exec fixture on spawn

        self._initialize_result = initialize_result or {"protocolVersion": 1}
        self._session_seq = 0
        self._srv_req_seq = 0
        self._prompts_by_session: dict[str, deque] = defaultdict(deque)
        self._default_prompts: deque = deque()
        self._pending_srv: dict[int, asyncio.Future] = {}

        # stdin: client → peer (parsed as requests). stdout/stderr: peer → client.
        self._in_reader = asyncio.StreamReader()
        self.stdin = _FakeStdin(self._in_reader)
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._stdout_eof = False

        self._exited = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._servicer = asyncio.ensure_future(self._serve())

    # -- Scripting ---------------------------------------------------------

    def script_prompt(
        self,
        *,
        updates=None,
        result=None,
        error=None,
        delay=0.0,
        server_request=None,
        die=False,
        session_id=None,
    ) -> "FakeAcpProc":
        """Queue one scripted answer for the next matching ``session/prompt``.

        Scripts are consumed FIFO — per ``session_id`` when given, else from a
        session-agnostic queue used in registration order. Returns ``self`` so
        registrations can be chained. See :class:`_PromptScript` for the fields.
        """
        script = _PromptScript(
            updates=list(updates or []),
            result=result,
            error=error,
            delay=delay,
            server_request=server_request,
            die=die,
        )
        if session_id is None:
            self._default_prompts.append(script)
        else:
            self._prompts_by_session[session_id].append(script)
        return self

    # -- Process surface ---------------------------------------------------

    async def wait(self) -> Optional[int]:
        """Block until the process exits (crash / kill / stdin EOF)."""
        await self._exited.wait()
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self._crash(-15)

    def kill(self) -> None:
        self.killed = True
        self._crash(-9)

    # -- Servicing ---------------------------------------------------------

    async def _serve(self) -> None:
        """Read inbound ndjson line-by-line, recording and dispatching each."""
        try:
            while True:
                line = await self._in_reader.readline()
                if not line:
                    break  # stdin EOF — the client closed the connection.
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    continue  # tolerate garbage, like a real peer would
                if not isinstance(msg, dict):
                    continue
                if msg.get("method") is None and msg.get("id") is not None:
                    # A response to one of our server→client requests.
                    self._resolve_server_response(msg)
                    continue
                self.requests.append(msg)
                self._spawn(self._handle(msg))
        except asyncio.CancelledError:
            raise
        finally:
            # The peer is gone: settle the exit so wait() never hangs and no
            # further response can be emitted onto a dead stdout.
            if self.returncode is None:
                self.returncode = 0
            self._stdout_eof = True
            with contextlib.suppress(Exception):
                self.stdout.feed_eof()
            with contextlib.suppress(Exception):
                self.stderr.feed_eof()
            self._exited.set()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            self._respond(msg_id, dict(self._initialize_result))
        elif method == "session/new":
            self._session_seq += 1
            self._respond(msg_id, {"sessionId": f"sess-{self._session_seq}"})
        elif method == "session/prompt":
            await self._handle_prompt(msg_id, params)
        elif msg_id is not None:
            # Unknown *request* — answer with method-not-found so nobody hangs.
            self._respond_error(
                msg_id, {"code": -32601, "message": f"method not found: {method}"}
            )
        # Unknown notifications (no id) are silently recorded-and-ignored.

    async def _handle_prompt(self, msg_id, params: dict) -> None:
        session_id = params.get("sessionId")
        script = self._next_prompt(session_id)
        if script is None:
            self._respond(msg_id, {"stopReason": "end_turn"})
            return
        if script.server_request is not None:
            await self._request_permission(session_id, script.server_request)
        for update in script.updates:
            self._emit_update(session_id, update)
            if script.delay:
                await asyncio.sleep(script.delay)
        if script.die:
            self._crash(-9)  # crash mid-prompt: no answer, stdout EOFs
            return
        if script.error is not None:
            self._respond_error(msg_id, script.error)
        else:
            result = script.result if script.result is not None else {"stopReason": "end_turn"}
            self._respond(msg_id, result)

    def _next_prompt(self, session_id) -> Optional[_PromptScript]:
        queue = self._prompts_by_session.get(session_id)
        if queue:
            return queue.popleft()
        if self._default_prompts:
            return self._default_prompts.popleft()
        return None

    async def _request_permission(self, session_id, spec: dict) -> dict:
        """Emit a server→client request and await the client's response."""
        self._srv_req_seq += 1
        req_id = 100_000 + self._srv_req_seq  # keep clear of client-side ids
        params = dict(spec.get("params") or {})
        params.setdefault("sessionId", session_id)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_srv[req_id] = fut
        self._emit(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": spec.get("method", "session/request_permission"),
                "params": params,
            }
        )
        return await fut

    def _resolve_server_response(self, msg: dict) -> None:
        self.client_responses.append(msg)
        fut = self._pending_srv.pop(msg.get("id"), None)
        if fut is not None and not fut.done():
            fut.set_result(msg)

    # -- Emission ----------------------------------------------------------

    def _emit_update(self, session_id, params: dict) -> None:
        params = dict(params)
        params.setdefault("sessionId", session_id)
        self._emit({"jsonrpc": "2.0", "method": "session/update", "params": params})

    def _respond(self, msg_id, result: dict) -> None:
        if msg_id is None:
            return
        self._emit({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _respond_error(self, msg_id, error: dict) -> None:
        if msg_id is None:
            return
        self._emit({"jsonrpc": "2.0", "id": msg_id, "error": error})

    def _emit(self, obj: dict) -> None:
        """Feed one ndjson line onto stdout (dropped once the process has exited)."""
        if self._stdout_eof:
            return
        self.stdout.feed_data((json.dumps(obj) + "\n").encode("utf-8"))

    # -- Crash -------------------------------------------------------------

    def _crash(self, returncode: int) -> None:
        """Simulate process death: set returncode, EOF stdout, unblock wait()."""
        if self.returncode is None:
            self.returncode = returncode
        self._stdout_eof = True
        with contextlib.suppress(Exception):
            self.stdout.feed_eof()
        with contextlib.suppress(Exception):
            self.stderr.feed_eof()
        self._exited.set()
        if not self._servicer.done():
            self._servicer.cancel()


@pytest.fixture
def fake_acp_exec():
    """Factory fixture for replacing ``asyncio.create_subprocess_exec``.

    Call the yielded factory with the :class:`FakeAcpProc` instances to hand out,
    in spawn order (or none, to auto-create a default proc per spawn)::

        exec_fn, created = fake_acp_exec(proc)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)

    ``exec_fn`` only intercepts ``hermes acp …`` spawns — it asserts ``"acp"`` is
    in the argv (and records it on ``proc.argv``) so any other subprocess use is
    surfaced as a loud failure rather than silently hijacked. ``created`` is the
    live list of handed-out procs, newest last; it is also exposed as
    ``exec_fn.created`` to mirror ``fake_exec_factory`` in test_hermes_tasks.py.
    """
    created: list = []

    def make(*procs):
        queue = list(procs)

        async def _exec(program, *args, **kwargs):
            argv = [program, *args]
            assert "acp" in argv, (
                f"fake_acp_exec only handles `hermes acp` spawns, got argv={argv!r}"
            )
            proc = queue.pop(0) if queue else FakeAcpProc()
            proc.argv = argv
            created.append(proc)
            return proc

        _exec.created = created
        return _exec, created

    return make
