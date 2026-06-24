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

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import websockets
from aiohttp import web

SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


class FakeTTSServer:
    """A real WebSocket server mimicking the Desktop /tts contract.

    On each client connection it reads the JSON request (recorded in
    ``received``), streams ``chunks`` as binary frames, then terminates the
    stream according to ``mode``:
        - "done"       → JSON {"done": true}
        - "error"      → JSON {"error": <error>}
        - "disconnect" → close the socket mid-stream (no terminal frame)
    """

    def __init__(self, chunks, *, mode="done", error="boom"):
        import asyncio

        self.chunks = chunks
        self.mode = mode
        self.error = error
        self.received = []  # request JSON dicts, one per client connection
        self.connections = 0
        # Set to True and signalled when the client-side socket closes.  Barge-in
        # abort tests can await disconnected_event to confirm the socket closed.
        self.disconnected = False
        self.disconnected_event = asyncio.Event()
        self._server = None
        self.url = None

    async def _handler(self, ws):
        self.connections += 1
        try:
            req = await ws.recv()
            self.received.append(json.loads(req))
            for chunk in self.chunks:
                await ws.send(chunk)
            if self.mode == "error":
                await ws.send(json.dumps({"error": self.error}))
            elif self.mode == "disconnect":
                await ws.close()
                return
            else:
                await ws.send(json.dumps({"done": True}))
            # Keep the handler alive so the client controls teardown; this lets
            # the test assert per-utterance client-side disconnect.
            try:
                await ws.wait_closed()
            except Exception:
                pass
        except websockets.exceptions.ConnectionClosed:
            # Client closed mid-stream (e.g. barge-in abort).
            pass
        finally:
            # Always mark the disconnect so tests can assert the socket closed.
            self.disconnected = True
            self.disconnected_event.set()

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

    async def _make(chunks, *, mode="done", error="boom"):
        srv = FakeTTSServer(chunks, mode=mode, error=error)
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
