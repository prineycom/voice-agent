"""GPU-free fake `/a2f` WebSocket server for TTS fork tests.

Mirrors the real A2F contract (infra/desktop/a2f/server.py and its tests):
per utterance the client sends an optional {"emotion": ...} text frame, binary
PCM16 frames, then {"end": true}; the server replies with a couple of
{"type": "blendshapes", ...} text frames + {"done": true}, and loops so a single
connection can carry multiple utterances.

Every received message is recorded into a thread-safe queue as a tagged event so
tests can assert ordering and audio-binding:
    ("emotion", value) | ("pcm", bytes) | ("end", True) | ("closed", None)

Runs `websockets.serve` on 127.0.0.1:0 inside a background thread with its own
event loop; `.url` exposes the bound address, `.events` the queue.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading

import websockets


class FakeA2F:
    def __init__(self) -> None:
        self.events: "queue.Queue" = queue.Queue()
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def url(self) -> str:
        assert self.port is not None, "FakeA2F not started"
        return f"ws://127.0.0.1:{self.port}/a2f"

    def drain_events(self) -> list:
        """Return every recorded event so far, oldest first, without blocking."""
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out

    async def _handler(self, ws) -> None:
        try:
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    self.events.put(("pcm", bytes(message)))
                    continue
                ctrl = json.loads(message)
                if "emotion" in ctrl:
                    self.events.put(("emotion", ctrl["emotion"]))
                if ctrl.get("end"):
                    self.events.put(("end", True))
                    # Reply with a short blendshape burst, then done.
                    for i in range(2):
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "blendshapes",
                                    "frame": i,
                                    "t": i / 30.0,
                                    "arkit": {"JawOpen": 0.1, "MouthSmileLeft": 0.2},
                                }
                            )
                        )
                    await ws.send(json.dumps({"done": True}))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.events.put(("closed", None))

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _start() -> None:
            self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()

        loop.run_until_complete(_start())
        loop.run_forever()
        # Loop stopped: shut the server and drain pending callbacks.
        loop.run_until_complete(self._shutdown())
        loop.close()

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def start(self) -> "FakeA2F":
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=10), "FakeA2F failed to start"
        return self

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = None
        self._thread = None

    def __enter__(self) -> "FakeA2F":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
