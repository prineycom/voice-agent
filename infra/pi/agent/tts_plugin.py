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
    Client → Server: JSON {"text": "...", "voice": "default", "emotion": "neutral"}
    Server → Client, PER SENTENCE, in order:
        - binary 16-bit PCM @24kHz mono chunks — audio (pushed to the AudioEmitter)
        - JSON {"type":"blendshapes","frame":<int>,"t":<sentence-relative float>,
          "arkit":{...}} — A2F facial frames (forwarded to the browser)
        - JSON {"done": true} — per-sentence AUDIO end (internal rendezvous only)
        - JSON {"type":"a2f_done"} — per-sentence A2F-stream end (internal only)
      (or JSON {"error": "..."} on failure)
The streaming path pipelines sentences over one socket and matches each `done` to a
sent sentence. Blendshape frames are re-based from sentence-relative `t` to
reply-relative `t` (accumulating each sentence's audio duration) and forwarded on
the `voiceagent` data channel; `done`/`a2f_done` are internal and never forwarded.
Exactly ONE reply-level {"done": true} is published per reply.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging

import websockets
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIError,
    tokenize,
    tts,
    utils,
)

from motion_events import normalize_emotion

log = logging.getLogger("agent")


def _swallow_task(task: "asyncio.Task") -> None:
    """Retrieve a fire-and-forget task's result so it never warns.

    Blendshape forwarding and the reply-`done` marker are lossy: a failed publish
    must never surface. Consuming the task's exception here keeps asyncio quiet.
    """
    try:
        task.exception()
    except Exception:  # noqa: BLE001 — best-effort, ignore
        pass

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
        # Lossy fire-and-forget publisher for the `voiceagent` data channel: the
        # streaming path forwards re-based A2F blendshape frames and one reply-level
        # {"done": true} through it. None = no-op (e.g. the one-shot path / tests).
        self._publish = None
        # Zero-arg getter returning the current emotion tag, read at flush time so
        # the latest expression before each sentence wins. None → "neutral".
        self._emotion_source = None

    def set_publisher(self, publish) -> None:
        """Set the `voiceagent` data-channel publisher (async or sync, bytes)."""
        self._publish = publish

    def set_emotion_source(self, getter) -> None:
        """Set the zero-arg getter for the current emotion tag."""
        self._emotion_source = getter

    def _current_emotion(self) -> str:
        """Current emotion for the outgoing request, clamped to the known enum.

        Reads the getter live (at send/flush time) so the latest tag wins; falls
        back to ``"neutral"`` when no source is set or it returns a falsy value.
        """
        source = self._emotion_source
        return normalize_emotion(source() if source is not None else None)

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
            # Bound the close handshake: on barge-in the recv task is already
            # cancelled, so if the server is mid-send the graceful close would
            # otherwise wait the full websockets close-timeout (~10s). Cap it so
            # interruption stays responsive — we forget the socket regardless.
            await asyncio.wait_for(ws.close(), timeout=2.0)
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
        """Close the persistent socket if open.

        Acquire the lock so shutdown waits for any in-flight stream to release the
        socket before tearing it down, mirroring how `_run` holds it for the
        duration of a stream. Best-effort: never raises.
        """
        async with self._lock:
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:  # noqa: BLE001 — best-effort teardown, ignore
                    pass
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
                json.dumps(
                    {
                        "text": self._input_text,
                        "voice": self._tts._voice,
                        "emotion": self._tts._current_emotion(),
                    }
                )
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
        # Guards the single reply-level {"done": true}: emitted once on normal
        # completion OR on the cancel/error path, whichever fires first.
        self._reply_done_sent = False

    def _publish_frame(self, payload: bytes) -> None:
        """Fire-and-forget a `voiceagent` frame; never raise, never block audio.

        A sync publisher is called inline; an async one is scheduled as a detached
        task whose result is swallowed. Any failure is ignored so a publish can
        never break the audio path.
        """
        publish = self._tts._publish
        if publish is None:
            return
        try:
            result = publish(payload)
            if asyncio.iscoroutine(result):
                task = asyncio.ensure_future(result)
                task.add_done_callback(_swallow_task)
        except Exception:  # noqa: BLE001 — lossy publish, ignore
            pass

    def _send_reply_done(self) -> None:
        """Publish exactly one reply-level {"done": true} (idempotent)."""
        if self._reply_done_sent:
            return
        self._reply_done_sent = True
        self._publish_frame(json.dumps({"done": True}).encode())

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid("tts_")
        # Reset per-reply state (this stream serves one reply).
        self._reply_done_sent = False

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
                        # Read the emotion getter AT FLUSH TIME so the latest tag
                        # parsed before this sentence wins; None/falsy → "neutral".
                        await ws.send(
                            json.dumps(
                                {
                                    "text": text,
                                    "voice": self._tts._voice,
                                    "emotion": self._tts._current_emotion(),
                                }
                            )
                        )
                        count += 1
                    total_sends = count
                    send_done.set()

                async def _recv() -> None:
                    nonlocal seen_done
                    # Carry a trailing odd byte across frames within one message so a
                    # 16-bit sample split across two WebSocket frames is never broken.
                    # Each `/tts` message is a self-contained PCM run, so the carry is
                    # reset at every `done` boundary (see below) — never spanning
                    # sentences, where it would parity-shift all subsequent audio.
                    leftover = b""
                    # Per-reply A2F state (reset here = reset per reply): count the
                    # per-sentence `a2f_done` markers in parallel with audio `done`,
                    # and re-base each sentence's blendshape `t` to reply-relative by
                    # accumulating each finished sentence's audio duration.
                    seen_a2f_done = 0
                    reply_offset_s = 0.0
                    sentence_bytes = 0  # PCM bytes pushed for the in-flight sentence
                    # FIFO of finalized per-sentence audio durations: each audio
                    # `done` APPENDS its sentence's duration, each `a2f_done` POPS the
                    # oldest and adds it to reply_offset_s. Pairing by FIFO order (not
                    # a single shared variable) keeps the offset correct even when a
                    # sentence's `a2f_done` arrives out of order — after a later
                    # sentence's audio `done` — because a background A2F fork drains
                    # independently of the audio stream.
                    sentence_durs_s: "collections.deque[float]" = collections.deque()
                    recv_task: asyncio.Task | None = None
                    send_done_task = asyncio.ensure_future(send_done.wait())
                    try:
                        while True:
                            if (
                                send_done.is_set()
                                and seen_done >= total_sends
                                and seen_a2f_done >= total_sends
                            ):
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
                                    sentence_bytes += aligned
                                leftover = buf[aligned:]
                                continue

                            data = json.loads(msg)
                            if data.get("error"):
                                raise APIError(f"TTS service error: {data['error']}")

                            msg_type = data.get("type")
                            if msg_type == "blendshapes":
                                # Re-base sentence-relative `t` to reply-relative and
                                # forward on the `voiceagent` channel (lossy). A
                                # malformed frame (missing/None key) must NEVER break
                                # audio — build the payload defensively and drop the
                                # frame on any error, exactly like a failed publish.
                                try:
                                    t = data["t"]
                                    payload = json.dumps(
                                        {
                                            "type": "blendshapes",
                                            "frame": data["frame"],
                                            "t": t + reply_offset_s,
                                            "arkit": data["arkit"],
                                        }
                                    ).encode()
                                except (KeyError, TypeError):
                                    # Malformed A2F frame — skip it; never touches the
                                    # audio path or reply_offset_s accounting.
                                    log.debug("dropping malformed blendshape frame")
                                    continue
                                self._publish_frame(payload)
                                continue
                            if msg_type == "a2f_done":
                                # Per-sentence A2F-stream end: advance the reply offset
                                # by the OLDEST unpaired sentence's finalized duration
                                # (FIFO pop) so the next sentence's `t` stays monotonic
                                # even under out-of-order fork completion. Empty FIFO →
                                # advance by 0.0 (defensive; must never raise).
                                # Internal only.
                                seen_a2f_done += 1
                                reply_offset_s += (
                                    sentence_durs_s.popleft() if sentence_durs_s else 0.0
                                )
                                continue

                            if data.get("done"):
                                if leftover:
                                    log.warning(
                                        "TTS message ended on an odd byte boundary; "
                                        "dropping %d trailing byte(s)",
                                        len(leftover),
                                    )
                                    leftover = b""
                                output_emitter.flush()
                                # Finalize this sentence's audio duration and queue it
                                # for the matching `a2f_done` (FIFO order) to apply.
                                # Internal only.
                                sentence_durs_s.append(
                                    sentence_bytes / 2 / SAMPLE_RATE
                                )
                                sentence_bytes = 0
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
                # Normal completion: exactly one reply-level {"done": true}.
                self._send_reply_done()
            except asyncio.CancelledError:
                # Barge-in/interruption: stop forwarding, emit the reply-level done
                # so the browser ends this reply's A2F stream, and drop the socket so
                # the next turn reconnects to a clean server-side state (mirrors STT).
                self._send_reply_done()
                await self._tts._drop_ws()
                raise
            except APIError:
                self._send_reply_done()
                await self._tts._drop_ws()
                raise
            except Exception as e:
                self._send_reply_done()
                await self._tts._drop_ws()
                raise APIError(f"TTS synthesis failed: {e}") from e
