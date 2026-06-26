"""TTS plugin — synthesizes speech over a WebSocket to the Desktop /tts service.

Two code paths share one `tts.TTS` subclass:

* One-shot (`streaming=False`): `synthesize()` returns a `ChunkedStream` that opens
  a fresh WebSocket per utterance and pushes raw PCM16 @24kHz mono into the
  framework's `AudioEmitter`. Kept as a simple, robust fallback.
* Streaming (`streaming=True`, default): `stream()` returns a
  `DesktopSynthesizeStream` that sentence-tokenizes the incoming text and
  pipelines those sentences over ONE persistent WebSocket reused across turns,
  draining PCM back in order. This shortens time-to-first-audio on long replies
  and yields smoother segment-to-segment playback. Barge-in (cancellation) drops
  the socket so a half-drained connection never leaks into the next turn.

The Desktop `/tts` contract (see infra/desktop/README.md):
    Client → Server: JSON {"text": "...", "voice": "default"}
    Server → Client: binary 16-bit PCM @24kHz mono chunks, then JSON {"done": true}
                      (or JSON {"error": "..."} on failure)
The contract is per-message: each sent sentence yields its own PCM run followed by
exactly one {"done": true}, so the streaming path can pipeline sentences over the
same socket and match each `done` to a sent sentence.
"""

from __future__ import annotations

import asyncio
import json

import websockets
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIError,
    tokenize,
    tts,
    utils,
)

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
# Raw PCM mime type so the emitter treats incoming bytes as already-decoded samples.
MIME_TYPE = "audio/pcm"


class DesktopTTS(tts.TTS):
    """TTS that talks to the Desktop /tts WebSocket service.

    With `streaming=True` (default) `stream()` reuses ONE persistent WebSocket per
    session and pipelines sentence-tokenized text over it. With `streaming=False`
    the one-shot `synthesize()`/`ChunkedStream` path opens a fresh socket per
    utterance. `ws_url` is injectable for tests.
    """

    def __init__(
        self,
        *,
        ws_url: str,
        voice: str = "default",
        sample_rate: int = SAMPLE_RATE,
        streaming: bool = True,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=streaming),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._ws_url = ws_url
        self._voice = voice
        # Persistent socket + lock guard the streaming path (one in-flight stream
        # per session). The same sentence tokenizer the framework's StreamAdapter
        # uses, so sentence boundaries match what the rest of the stack expects.
        self._lock = asyncio.Lock()
        self._ws: websockets.ClientConnection | None = None
        self._tokenizer = tokenize.blingfire.SentenceTokenizer(retain_format=True)

    async def _ensure_ws(self) -> websockets.ClientConnection:
        """Lazily open (or reopen) the persistent socket. Caller holds the lock."""
        if self._ws is None:
            self._ws = await websockets.connect(self._ws_url, max_size=None)
        return self._ws

    async def _drop_ws(self) -> None:
        """Close and forget the persistent socket (best-effort). Never raises.

        Used on the cancellation/error path: a half-drained socket must not leak
        into the next turn, so dropping it forces a clean reconnect next stream.
        """
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 — best-effort teardown, ignore
            pass

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedStream":
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "DesktopSynthesizeStream":
        return DesktopSynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        """Close the persistent socket if open."""
        if self._ws is not None:
            try:
                await self._ws.close()
            finally:
                self._ws = None


class ChunkedStream(tts.ChunkedStream):
    """Streams one synthesized utterance from the Desktop /tts WebSocket."""

    def __init__(
        self,
        *,
        tts: DesktopTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: DesktopTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid("tts_")
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts._sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type=MIME_TYPE,
        )

        ws = await websockets.connect(self._tts._ws_url, max_size=None)
        try:
            await ws.send(
                json.dumps({"text": self._input_text, "voice": self._tts._voice})
            )

            # Carry a trailing odd byte across chunks so a 16-bit sample is never split.
            leftover = b""
            while True:
                msg = await ws.recv()
                if isinstance(msg, (bytes, bytearray)):
                    buf = leftover + bytes(msg)
                    # Push only complete-sample runs (2 bytes/sample).
                    aligned = len(buf) - (len(buf) % 2)
                    if aligned:
                        output_emitter.push(buf[:aligned])
                    leftover = buf[aligned:]
                    continue

                data = json.loads(msg)
                if data.get("error"):
                    raise APIError(f"TTS service error: {data['error']}")
                if data.get("done"):
                    break

            output_emitter.flush()
        finally:
            await ws.close()


class DesktopSynthesizeStream(tts.SynthesizeStream):
    """Streams synthesized speech over the persistent Desktop /tts WebSocket.

    Incoming text is sentence-tokenized; each sentence is sent over one socket and
    its PCM is drained back in order. Three coroutines pipeline the work: one feeds
    text into the tokenizer, one sends completed sentences, one drains PCM/done
    frames. On cancellation (barge-in) the socket is dropped so a half-drained
    connection never bleeds into the next turn.
    """

    def __init__(
        self,
        *,
        tts: DesktopTTS,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: DesktopTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid("tts_")

        # One in-flight stream per session: the lock serialises access to the
        # persistent socket so two turns can't interleave on the same connection.
        async with self._tts._lock:
            try:
                ws = await self._tts._ensure_ws()

                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=self._tts._sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type=MIME_TYPE,
                    stream=True,
                )
                output_emitter.start_segment(segment_id=utils.shortuuid())

                sent_stream = self._tts._tokenizer.stream()

                # send/recv rendezvous: the server emits exactly one {"done": true}
                # per sentence sent, so recv stops once it has drained `total_sends`
                # done frames. `total_sends` is only final after send completes.
                send_done = asyncio.Event()
                total_sends = 0
                seen_done = 0

                async def _feed() -> None:
                    async for data in self._input_ch:
                        if isinstance(data, self._FlushSentinel):
                            sent_stream.flush()
                            continue
                        sent_stream.push_text(data)
                    sent_stream.end_input()

                async def _send() -> None:
                    nonlocal total_sends
                    count = 0
                    async for ev in sent_stream:
                        if not (text := ev.token.strip()):
                            continue
                        await ws.send(
                            json.dumps({"text": text, "voice": self._tts._voice})
                        )
                        count += 1
                    total_sends = count
                    send_done.set()

                async def _recv() -> None:
                    nonlocal seen_done
                    # Carry a trailing odd byte across frames so a 16-bit sample is
                    # never split (PCM runs from consecutive sentences are contiguous).
                    leftover = b""
                    recv_task: asyncio.Task | None = None
                    send_done_task = asyncio.ensure_future(send_done.wait())
                    try:
                        while True:
                            if send_done.is_set() and seen_done >= total_sends:
                                break

                            if recv_task is None:
                                recv_task = asyncio.ensure_future(ws.recv())

                            if send_done.is_set():
                                # Total is known; just drain the remaining frames.
                                msg = await recv_task
                                recv_task = None
                            else:
                                await asyncio.wait(
                                    {recv_task, send_done_task},
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                if not recv_task.done():
                                    # send finished first — re-check the exit condition.
                                    continue
                                msg = recv_task.result()
                                recv_task = None

                            if isinstance(msg, (bytes, bytearray)):
                                buf = leftover + bytes(msg)
                                aligned = len(buf) - (len(buf) % 2)
                                if aligned:
                                    output_emitter.push(buf[:aligned])
                                leftover = buf[aligned:]
                                continue

                            data = json.loads(msg)
                            if data.get("error"):
                                raise APIError(f"TTS service error: {data['error']}")
                            if data.get("done"):
                                output_emitter.flush()
                                seen_done += 1
                    finally:
                        if recv_task is not None:
                            recv_task.cancel()
                        send_done_task.cancel()

                tasks = [
                    asyncio.create_task(_feed()),
                    asyncio.create_task(_send()),
                    asyncio.create_task(_recv()),
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    await sent_stream.aclose()
                    await utils.aio.cancel_and_wait(*tasks)
            except asyncio.CancelledError:
                # Barge-in/interruption: drop the socket so the next turn reconnects
                # to a clean server-side state (mirrors the STT plugin).
                await self._tts._drop_ws()
                raise
            except APIError:
                await self._tts._drop_ws()
                raise
            except Exception as e:
                await self._tts._drop_ws()
                raise APIError(f"TTS synthesis failed: {e}") from e
