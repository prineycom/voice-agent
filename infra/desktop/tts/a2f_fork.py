"""Best-effort fork of TTS audio into the co-located A2F service (Epic 8, Phase 1).

Each `/tts` utterance is teed into the A2F `/a2f` WebSocket (loopback) so A2F
produces ARKit blendshapes bound to the exact audio that reached the client.

In Phase 1 the fork PUSHES emotion + PCM16@24k + {"end": true} and DRAINS/DISCARDS
the returned blendshape frames. It is strictly best-effort: any A2F failure is
logged at WARNING and swallowed — it must NEVER affect the `/tts` client stream
or barge-in.

Protocol mirrored from infra/desktop/a2f/server.py (per utterance, in order):
    1. optional JSON {"emotion": "happy"} or {"emotion": [10 floats]}
    2. binary PCM16 @ 24 kHz mono frames
    3. JSON {"end": true}
then the server streams {"type": "blendshapes", ...} frames + {"done": true}.
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

    def __init__(self, url: str, emotion) -> None:
        self.url = url
        self.emotion = emotion
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
                if self.emotion is not None:
                    await a2f.send(json.dumps({"emotion": self.emotion}))
                while True:
                    item = await self._queue.get()
                    if item is _SENTINEL:
                        break
                    await a2f.send(item)
                await a2f.send(json.dumps({"end": True}))
                # Drain and discard the blendshape frames until done/error.
                while True:
                    msg = await a2f.recv()
                    if isinstance(msg, str):
                        data = json.loads(msg)
                        if data.get("done") or "error" in data:
                            break
        except Exception:  # noqa: BLE001 — best-effort; A2F must not break /tts
            self._failed = True
            log.warning("A2F fork failed; dropping blendshapes for this utterance", exc_info=True)


def maybe_start_fork(emotion) -> "A2FFork | None":
    """Create an :class:`A2FFork` if enabled and configured, else return None.

    Must be called from within a running event loop (the `/tts` handler is async).
    """
    if os.getenv("A2F_FORK_ENABLED", "1").strip().lower() in _DISABLED_VALUES:
        return None
    url = os.getenv("A2F_WS_URL", DEFAULT_A2F_WS_URL).strip()
    if not url:
        return None
    return A2FFork(url, emotion)
