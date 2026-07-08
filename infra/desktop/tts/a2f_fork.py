"""Best-effort fork of TTS audio into the co-located A2F service (Epic 8, Phase 1).

Each `/tts` utterance is teed into the A2F `/a2f` WebSocket (loopback) so A2F
produces ARKit blendshapes bound to the exact audio that reached the client.

The fork PUSHES emotion + PCM16@24k + {"end": true} and FORWARDS the returned
blendshape frames verbatim via an injected `on_frame` callback; send and
receive run concurrently on the same connection, so frames are forwarded as
they arrive — possibly while PCM is still streaming. A2F's own {"done": true}
is suppressed and instead signalled once via `on_done` when the stream ends
for any reason (drain, failure, or cancel). It is strictly best-effort: any
A2F failure is logged at WARNING and swallowed — it must NEVER affect the
`/tts` client stream or barge-in.

Protocol mirrored from infra/desktop/a2f/server.py (per utterance, send order):
    1. optional JSON {"emotion": "happy"} or {"emotion": [10 floats]}
    2. binary PCM16 @ 24 kHz mono frames
    3. JSON {"end": true}
The server streams {"type": "blendshapes", ...} frames + {"done": true};
frames may arrive at any point once audio starts flowing, not only after end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets

log = logging.getLogger("tts.a2f_fork")

_SENTINEL = None

DEFAULT_A2F_WS_URL = "ws://127.0.0.1:8003/a2f"
_DISABLED_VALUES = {"0", "false", "no", "off"}


class A2FFork:
    """Owns a background task that streams one utterance's PCM into A2F.

    Fed synchronously from the `/tts` drain loop via :meth:`feed`; ends the
    utterance with :meth:`end`; torn down (barge-in or connection close) with
    :meth:`close`. Never blocks or raises to the caller.
    """

    def __init__(self, url: str, emotion, on_frame=None, on_done=None) -> None:
        self.url = url
        self.emotion = emotion
        self._on_frame = on_frame if on_frame is not None else (lambda frame: None)
        self._on_done = on_done if on_done is not None else (lambda: None)
        self._done_fired = False
        self._queue: asyncio.Queue = asyncio.Queue()
        self._failed = False
        self._ended = False
        self._task = asyncio.create_task(self._run())

    @property
    def done(self) -> bool:
        """True once the background task has finished (drained, failed, or closed)
        — safe to drop the reference. Lets the caller prune completed forks so a
        long-lived `/tts` connection doesn't retain one per utterance."""
        return self._task is None or self._task.done()

    def feed(self, pcm: bytes) -> None:
        """Enqueue a PCM chunk for A2F. No-op after failure or end. Never raises."""
        if self._failed or self._ended:
            return
        try:
            self._queue.put_nowait(pcm)
        except Exception:  # noqa: BLE001 — best-effort; never surface to caller
            log.debug("A2F fork feed dropped a chunk", exc_info=True)

    def end(self) -> None:
        """Signal end-of-utterance so the fork sends {"end": true}. No-op after failure."""
        if self._failed or self._ended:
            return
        self._ended = True
        try:
            self._queue.put_nowait(_SENTINEL)
        except Exception:  # noqa: BLE001
            log.debug("A2F fork end signal dropped", exc_info=True)

    async def close(self) -> None:
        """Idempotent teardown. Wait briefly for a graceful end, else cancel."""
        task = self._task
        if task is None:
            return
        self._task = None
        if self._ended and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                return
            except asyncio.TimeoutError:
                log.debug("A2F fork drain timed out; cancelling")
            except Exception:  # noqa: BLE001
                return
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def _run(self) -> None:
        try:
            async with websockets.connect(self.url, max_size=None) as a2f:
                # Interleave send and receive on the same connection so
                # blendshape frames forward as they arrive, while PCM is still
                # streaming. Normally the sender finishes on the sentinel and
                # the receiver on A2F's {"done": true} that follows it.
                sender = asyncio.ensure_future(self._send_loop(a2f))
                receiver = asyncio.ensure_future(self._recv_loop(a2f))
                try:
                    await asyncio.gather(sender, receiver)
                finally:
                    # If one side raised, gather propagates the first exception
                    # while the sibling task keeps running — reap it so nothing
                    # outlives the connection. Cancelling a finished task is a
                    # no-op. On close() cancellation this also kills both sides.
                    for side in (sender, receiver):
                        side.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        except Exception:  # noqa: BLE001 — best-effort; A2F must not break /tts
            self._failed = True
            log.warning("A2F fork failed; dropping blendshapes for this utterance", exc_info=True)
        finally:
            self._fire_done()

    async def _send_loop(self, a2f) -> None:
        """Push emotion + queued PCM, then {"end": true} on the sentinel."""
        if self.emotion is not None:
            await a2f.send(json.dumps({"emotion": self.emotion}))
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                break
            await a2f.send(item)
        await a2f.send(json.dumps({"end": True}))

    async def _recv_loop(self, a2f) -> None:
        # Forward each blendshape frame; stop on A2F's done/error without
        # forwarding it (on_done marks the end of the stream instead).
        while True:
            msg = await a2f.recv()
            if isinstance(msg, str):
                data = json.loads(msg)
                if data.get("type") == "blendshapes":
                    try:
                        self._on_frame(data)
                    except Exception:  # noqa: BLE001 — a raising consumer must not kill the drain loop
                        log.debug("A2F fork on_frame callback raised", exc_info=True)
                elif data.get("done") or "error" in data:
                    break

    def _fire_done(self) -> None:
        """Invoke on_done exactly once (on drain, failure, or cancel)."""
        if self._done_fired:
            return
        self._done_fired = True
        try:
            self._on_done()
        except Exception:  # noqa: BLE001 — best-effort; never surface to caller
            log.debug("A2F fork on_done callback raised", exc_info=True)


def maybe_start_fork(emotion, on_frame=None, on_done=None) -> "A2FFork | None":
    """Create an :class:`A2FFork` if enabled and configured, else return None.

    Must be called from within a running event loop (the `/tts` handler is async).
    """
    if os.getenv("A2F_FORK_ENABLED", "1").strip().lower() in _DISABLED_VALUES:
        return None
    url = os.getenv("A2F_WS_URL", DEFAULT_A2F_WS_URL).strip()
    if not url:
        return None
    return A2FFork(url, emotion, on_frame=on_frame, on_done=on_done)
