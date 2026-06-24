"""TTS plugin — synthesizes one utterance over a fresh WebSocket to the Desktop /tts service.

Implements the livekit-agents 1.6.x non-streamed TTS API: a `tts.TTS` subclass
whose `synthesize()` returns a `ChunkedStream` that pushes raw PCM16 @24kHz mono
into the framework's `AudioEmitter` (the framework handles framing/resampling).

The Desktop `/tts` contract (see infra/desktop/README.md):
    Client → Server: JSON {"text": "...", "voice": "default"}
    Server → Client: binary 16-bit PCM @24kHz mono chunks, then JSON {"done": true}
                      (or JSON {"error": "..."} on failure)
"""

from __future__ import annotations

import json

import websockets
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIError,
    tts,
    utils,
)

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
# Raw PCM mime type so the emitter treats incoming bytes as already-decoded samples.
MIME_TYPE = "audio/pcm"


class DesktopTTS(tts.TTS):
    """One-shot TTS that talks to the Desktop /tts WebSocket service.

    A fresh WebSocket is opened per utterance; `ws_url` is injectable for tests.
    """

    def __init__(
        self, *, ws_url: str, voice: str = "default", sample_rate: int = SAMPLE_RATE
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._ws_url = ws_url
        self._voice = voice

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedStream":
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


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
