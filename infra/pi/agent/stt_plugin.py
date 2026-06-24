"""STT plugin — transcribes one utterance over a persistent WebSocket to the Desktop /stt service.

Implements the livekit-agents 1.6.x non-streaming STT API: an `stt.STT` subclass
whose `recognize()` (via `_recognize_impl`) sends the buffered utterance as raw
PCM16 @16kHz mono and returns a single final transcript.

Unlike the TTS plugin (one socket per utterance), this keeps ONE persistent socket
per session and reuses it across turns (ADR-0006). A best-effort `{"event":"reset"}`
is sent on failure so a half-buffer never leaks into the next turn.

The Desktop `/stt` contract (see infra/desktop/stt/server.py):
    Client → Server: binary 16-bit PCM @16kHz mono chunks, then JSON {"event":"end"}
                      (or JSON {"event":"reset"} to discard the current buffer)
    Server → Client: JSON {"text": "...", "is_final": false, "partial": "..."} for interim
                      results, then JSON {"text": "...", "is_final": true, "partial": ""}
                      once {"event":"end"} flushes (or JSON {"error": "..."} on failure)
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from livekit import rtc
from livekit.agents import APIConnectOptions, APIError, stt, utils
from livekit.agents.types import NOT_GIVEN, NotGivenOr

log = logging.getLogger("agent")

SAMPLE_RATE = 16000
NUM_CHANNELS = 1


class DesktopSTT(stt.STT):
    """Non-streaming STT that talks to the Desktop /stt WebSocket service.

    One persistent WebSocket is kept open per session and reused across turns;
    `ws_url` is injectable for tests.
    """

    def __init__(
        self, *, ws_url: str, language: str = "ru", sample_rate: int = SAMPLE_RATE
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._ws_url = ws_url
        self._language = language
        self._sample_rate = sample_rate
        self._lock = asyncio.Lock()
        self._ws: websockets.ClientConnection | None = None

    async def _ensure_ws(self) -> websockets.ClientConnection:
        """Lazily open (or reopen) the persistent socket. Caller holds the lock."""
        if self._ws is None:
            self._ws = await websockets.connect(self._ws_url, max_size=None)
        return self._ws

    async def _reset_buffer(self) -> None:
        """Best-effort discard of any half-buffer on the server. Never raises."""
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"event": "reset"}))
        except Exception:  # noqa: BLE001 — secondary failure, ignore
            # The socket is likely dead; drop it so the next turn reconnects.
            self._ws = None

    async def _drop_ws(self) -> None:
        """Close and forget the persistent socket (best-effort). Never raises.

        Used on the cancellation path: dropping the socket forces a fresh reconnect
        next turn, which the /stt server serves from an empty per-connection buffer.
        """
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 — best-effort teardown, ignore
            pass

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        async with self._lock:
            try:
                ws = await self._ensure_ws()

                # Merge the buffer (handles both a single frame and a list).
                frame = utils.merge_frames(buffer)

                # This pipeline is mono end to end: the LiveKit mic input is mono
                # and the /stt server expects mono PCM16. Both the resample and the
                # raw-passthrough branch below assume one channel, so a multi-channel
                # frame would be mis-resampled/garbled. Surface the misconfiguration
                # loudly rather than send corrupt audio to the server.
                if frame.num_channels != NUM_CHANNELS:
                    raise APIError(
                        f"STT expects mono audio (got {frame.num_channels} channels); "
                        "the LiveKit mic input in this pipeline is mono"
                    )

                # Resample to 16 kHz mono if needed.
                if frame.sample_rate != self._sample_rate:
                    resampler = rtc.AudioResampler(
                        frame.sample_rate,
                        self._sample_rate,
                        num_channels=NUM_CHANNELS,
                        quality=rtc.AudioResamplerQuality.VERY_HIGH,
                    )
                    out_frames = resampler.push(frame)
                    out_frames += resampler.flush()
                    pcm = b"".join(f.data.cast("b").tobytes() for f in out_frames)
                else:
                    pcm = frame.data.cast("b").tobytes()

                # Send the audio, then flush with an end event. Note: the
                # StreamAdapter retries recognize() (max_retry=3 in production), so a
                # transient failure re-uploads the whole buffer plus a fresh `end`
                # after the prior `reset`. This is functionally correct (reset clears
                # the server buffer first) but re-sends the full utterance per retry.
                await ws.send(pcm)
                await ws.send(json.dumps({"event": "end"}))

                # Read frames until a final transcript (or an error) arrives.
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, (bytes, bytearray)):
                        # The /stt service never sends binary; ignore defensively.
                        continue
                    data = json.loads(msg)
                    if data.get("error"):
                        raise APIError(f"STT service error: {data['error']}")
                    if data.get("is_final"):
                        text = data.get("text", "")
                        break
                    # Interim partial result — ignore.
                    log.debug("STT partial: %s", data.get("partial", ""))
            except asyncio.CancelledError:
                # Normal barge-in/interruption (ADR-0006). Do NOT rely on an in-band
                # `reset` send here: it may itself be cancelled before reaching the
                # server, leaving a half-buffer for the next turn. Instead drop the
                # socket entirely (best-effort close). The /stt server keeps a
                # per-connection buffer, so the next turn's fresh reconnect starts
                # with a clean, empty server buffer — the robust guarantee we want.
                await self._drop_ws()
                raise
            except APIError:
                await self._reset_buffer()
                raise
            except Exception as e:
                await self._reset_buffer()
                raise APIError(f"STT recognition failed: {e}") from e

        out_language = language if isinstance(language, str) else self._language
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=utils.shortuuid("stt_"),
            alternatives=[stt.SpeechData(language=out_language, text=text)],
        )

    async def aclose(self) -> None:
        """Close the persistent socket if open."""
        if self._ws is not None:
            try:
                await self._ws.close()
            finally:
                self._ws = None
