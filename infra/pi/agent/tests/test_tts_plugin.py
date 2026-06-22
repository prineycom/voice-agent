"""Behavioral tests for DesktopTTS against a real fake /tts WebSocket server.

No GPU, no SFU — a `websockets.serve` server on localhost speaks the Desktop
/tts protocol, and we drive the real livekit-agents 1.6.x synthesize path,
collecting the emitted AudioFrames.

Note on sample counts: at the end of a segment the framework's AudioEmitter
appends a small synthetic silence marker (~10ms / 240 samples @24kHz) after the
real audio. The bytes the plugin actually pushed are therefore always emitted
*intact, in order, as the exact non-silent prefix* of the output — which is the
property these tests assert (no sample lost, none split across chunk
boundaries). Test payloads end in a non-zero byte so stripping trailing silence
unambiguously isolates the real audio from the framework marker.
"""

import pytest
from livekit.agents import APIError

from tts_plugin import NUM_CHANNELS, SAMPLE_RATE, DesktopTTS


async def _collect(stream):
    """Consume a ChunkedStream → (frames, total_samples, concatenated_bytes)."""
    frames = []
    async for ev in stream:
        frames.append(ev.frame)
    await stream.aclose()
    total = sum(f.samples_per_channel for f in frames)
    data = b"".join(bytes(f.data) for f in frames)
    return frames, total, data


@pytest.mark.asyncio
async def test_request_json_and_audio_format(tts_server_factory):
    # 4 chunks of 4 bytes each = 16 bytes = 8 samples; ends in non-zero (0x10).
    chunks = [b"\x01\x02\x03\x04", b"\x05\x06\x07\x08",
              b"\x09\x0a\x0b\x0c", b"\x0d\x0e\x0f\x10"]
    pushed = b"".join(chunks)
    srv = await tts_server_factory(chunks)

    tts_impl = DesktopTTS(ws_url=srv.url, voice="narrator")
    frames, total_samples, data = await _collect(tts_impl.synthesize("hello world"))

    # The server saw exactly the request the plugin promised.
    assert srv.received == [{"text": "hello world", "voice": "narrator"}]

    # Emitted audio is 24kHz mono.
    assert frames, "expected at least one emitted frame"
    for f in frames:
        assert f.sample_rate == SAMPLE_RATE == 24000
        assert f.num_channels == NUM_CHANNELS == 1

    # Real audio == bytes pushed (none lost), recovered by stripping the
    # framework's trailing end-of-segment silence marker.
    real = data.rstrip(b"\x00")
    assert real == pushed
    assert len(real) // 2 == 8  # total real samples == total_pcm_bytes / 2
    # The output is exactly the real audio plus framework silence.
    assert total_samples * 2 == len(data)
    assert data.startswith(pushed)


@pytest.mark.asyncio
async def test_odd_length_chunk_boundaries_carry_leftover(tts_server_factory):
    # Two 3-byte chunks: each is odd, but the total (6 bytes = 3 samples) is
    # even. A correct leftover-carry yields all 3 samples with none split/lost.
    chunks = [b"\x01\x02\x03", b"\x04\x05\x06"]
    srv = await tts_server_factory(chunks)

    tts_impl = DesktopTTS(ws_url=srv.url)
    _, _, data = await _collect(tts_impl.synthesize("odd"))

    # The full 6 bytes survive intact and in order (everything after is the
    # framework's silence marker).
    real = data.rstrip(b"\x00")
    assert real == b"\x01\x02\x03\x04\x05\x06"
    assert len(real) // 2 == 3


@pytest.mark.asyncio
async def test_error_frame_raises_api_error(tts_server_factory):
    srv = await tts_server_factory([b"\x01\x02"], mode="error", error="kaboom")

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.synthesize("fail please")

    with pytest.raises(APIError):
        await _collect(stream)


@pytest.mark.asyncio
async def test_websocket_torn_down_per_utterance(tts_server_factory):
    # Each synthesize opens a FRESH connection; after two utterances against the
    # same server, the server must have accepted exactly two connections (and
    # each handler exits only when its client socket disconnects).
    chunks = [b"\x01\x02\x03\x04"]
    srv = await tts_server_factory(chunks)

    tts_impl = DesktopTTS(ws_url=srv.url)

    _, _, d1 = await _collect(tts_impl.synthesize("first"))
    assert d1.rstrip(b"\x00") == b"\x01\x02\x03\x04"

    _, _, d2 = await _collect(tts_impl.synthesize("second"))
    assert d2.rstrip(b"\x00") == b"\x01\x02\x03\x04"

    assert srv.connections == 2
    assert srv.received == [
        {"text": "first", "voice": "default"},
        {"text": "second", "voice": "default"},
    ]
