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

import asyncio

import pytest
import websockets
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
async def test_disconnect_midstream_raises(tts_server_factory):
    # The server drops the socket mid-stream (no terminal {"done": true}); the
    # plugin's `await ws.recv()` loop then surfaces the dropped connection.
    srv = await tts_server_factory([b"\x01\x02", b"\x03\x04"], mode="disconnect")

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.synthesize("drop me")

    with pytest.raises(websockets.ConnectionClosed):
        await _collect(stream)

    # The server accepted exactly one connection (and then closed it itself).
    assert srv.connections == 1


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


# A payload large enough that the stream is still in-flight after the first
# emitted frame: many big chunks (1 MiB each) the consumer cannot drain in the
# window between receiving frame #1 and us cancelling. Ends in a non-zero byte
# so it can never be mistaken for the framework's trailing silence.
_BIG_CHUNK = (b"\xab" * (1 << 20)) + b"\x10"
_MANY_BIG_CHUNKS = [_BIG_CHUNK] * 32


@pytest.mark.asyncio
async def test_cancel_midstream_closes_socket(tts_server_factory):
    # Barge-in: the framework cancels the synthesis mid-stream. `aclose()`
    # cancels the running `_run`, whose `finally: await ws.close()` is the ONLY
    # mechanism that aborts the GPU producer (there is no in-band stop frame).
    srv = await tts_server_factory(_MANY_BIG_CHUNKS)

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.synthesize("interrupt me")

    aiter = stream.__aiter__()
    # Consume exactly the first frame — the stream is now mid-flight.
    first = await asyncio.wait_for(aiter.__anext__(), timeout=2)
    assert first.frame is not None

    # The socket must NOT have closed yet: we are genuinely mid-stream, not at a
    # normal completion that just happened to fire `disconnected_event`.
    assert srv.disconnected is False

    # Abort mid-stream (this is what a real barge-in does).
    await asyncio.wait_for(stream.aclose(), timeout=2)

    # Closing the socket is what aborts: the server observes the disconnect
    # promptly, while the stream was still in-flight.
    await asyncio.wait_for(srv.disconnected_event.wait(), timeout=2)
    assert srv.disconnected is True

    # It really was mid-stream: the terminal {"done": true} was never seen, so
    # not all frames were consumed (we only ever pulled the first one).
    drained = []
    try:
        async for ev in aiter:
            drained.append(ev)
    except (asyncio.CancelledError, websockets.ConnectionClosed):
        pass
    assert not drained, "stream should have been aborted, not drained to completion"


@pytest.mark.asyncio
async def test_rapid_interruptions_no_leak(tts_server_factory):
    # Several back-to-back barge-ins on the same DesktopTTS instance must not
    # hang or leak sockets: each utterance opens a FRESH socket, each cycle
    # aborts cleanly, and the whole burst stays bounded in time.
    srv = await tts_server_factory(_MANY_BIG_CHUNKS)
    tts_impl = DesktopTTS(ws_url=srv.url)

    cycles = 5

    async def burst():
        for _ in range(cycles):
            stream = tts_impl.synthesize("barge in")
            aiter = stream.__aiter__()
            first = await aiter.__anext__()
            assert first.frame is not None
            await stream.aclose()

    # No hang/wedge: the entire burst completes within a bounded time.
    await asyncio.wait_for(burst(), timeout=10)

    # Fresh socket per utterance — connections increments once per cycle.
    assert srv.connections == cycles

    # Pipeline is not wedged: a normal synthesis still works after the burst.
    done_srv = await tts_server_factory([b"\x01\x02\x03\x04"])
    ok_tts = DesktopTTS(ws_url=done_srv.url)
    _, _, data = await asyncio.wait_for(
        _collect(ok_tts.synthesize("recovered")), timeout=5
    )
    assert data.rstrip(b"\x00") == b"\x01\x02\x03\x04"
