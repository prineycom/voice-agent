"""Test fixtures for the Agent Worker TTS plugin and health gate.

Puts the service dir on sys.path (so `import tts_plugin`/`import health`/
`import config` resolve regardless of cwd) and provides real fake servers
(no GPU, no SFU) over real sockets:

- a fake `/tts` WebSocket server (`websockets.serve` on an ephemeral port) that
  streams a configurable sequence of PCM16 chunks then `{"done": true}`, with
  variants for odd-length chunks, an `{"error": ...}` frame, and a mid-stream
  disconnect; it records the request JSON each client sends.
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
        self.chunks = chunks
        self.mode = mode
        self.error = error
        self.received = []  # request JSON dicts, one per client connection
        self.connections = 0
        self._server = None
        self.url = None

    async def _handler(self, ws):
        self.connections += 1
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
        # Keep the handler alive so the client controls teardown; this lets the
        # test assert per-utterance client-side disconnect.
        try:
            await ws.wait_closed()
        except Exception:
            pass

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
