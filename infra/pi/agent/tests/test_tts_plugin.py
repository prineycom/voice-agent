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
from livekit.agents import APIConnectOptions, APIError

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


async def _drive(stream, text):
    """Feed one turn of text into a SynthesizeStream and collect its audio.

    Pushes the whole turn in a single segment (the persistent streaming path
    sentence-tokenizes it internally), ends input, then drains the emitted
    frames → (frames, total_samples, concatenated_bytes).
    """
    stream.push_text(text)
    stream.end_input()
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
    consumed = 1

    # The socket must NOT have closed yet: we are genuinely mid-stream, not at a
    # normal completion that just happened to fire `disconnected_event`.
    assert srv.disconnected is False

    # Abort mid-stream (this is what a real barge-in does).
    await asyncio.wait_for(stream.aclose(), timeout=2)

    # The real proof of abort: the server's handler `finally` fired (socket
    # closed by `_run`'s own `finally: await ws.close()`), which is the only
    # mechanism that stops the GPU producer. `disconnected_event` firing == the
    # socket was closed by the abort path.
    await asyncio.wait_for(srv.disconnected_event.wait(), timeout=2)
    assert srv.disconnected is True

    # And it really was mid-stream, not a full drain: the frames consumed before
    # the cancel are a tiny fraction of what a completed stream would emit. With
    # 32 chunks of >1 MiB each a finished synthesis yields far more than a
    # handful of frames; we pulled only the first.
    assert consumed == 1
    assert consumed < len(_MANY_BIG_CHUNKS)


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

    # No leak: every socket actually CLOSED. The handler `finally` (which
    # decrements `active`) runs asynchronously after each `aclose()`, so poll
    # with a bounded wait — no fixed sleeps — until the gauge drains to zero.
    async def _wait_drained():
        while srv.active != 0:
            await asyncio.sleep(0)

    await asyncio.wait_for(_wait_drained(), timeout=2)
    assert srv.active == 0

    # Pipeline is not wedged: a normal synthesis still works after the burst.
    done_srv = await tts_server_factory([b"\x01\x02\x03\x04"])
    ok_tts = DesktopTTS(ws_url=done_srv.url)
    _, _, data = await asyncio.wait_for(
        _collect(ok_tts.synthesize("recovered")), timeout=5
    )
    assert data.rstrip(b"\x00") == b"\x01\x02\x03\x04"


# --- Streaming path (DesktopTTS(streaming=True).stream()) -------------------
#
# These drive the persistent DesktopSynthesizeStream: text is sentence-tokenized
# and pipelined over ONE reused WebSocket. Each non-empty sentence becomes its
# own {"text","voice"} request and yields its own PCM run + {"done": true}, all
# over the same connection.

# Distinct, recoverable PCM per sentence: each ends in a non-zero byte so the
# framework's trailing end-of-segment silence can be stripped to recover the
# exact bytes, and the two runs are byte-distinguishable to prove ordering.
# Even length (a complete 16-bit PCM run, as the real /tts server emits), so the
# within-message odd-byte carry never spills across the {"done"} boundary.
_S1_PCM = (b"\xaa" * 199) + b"\x11"
_S2_PCM = (b"\xbb" * 199) + b"\x22"


def _odd_chunks(payload):
    # Split a complete (even-length) PCM run into two odd-length chunks so a
    # 16-bit sample straddles the chunk boundary. Exercises the within-message
    # leftover carry; the run total stays even so nothing carries across `done`.
    return [payload[:99], payload[99:]]


@pytest.mark.asyncio
async def test_streaming_two_sentences_one_connection(tts_server_factory):
    # Two sentences over ONE persistent socket: exactly one connection accepted,
    # both request dicts received with the right text, PCM emitted in order.
    srv = await tts_server_factory(
        lambda i: [_S1_PCM] if i == 0 else [_S2_PCM], loop=True
    )

    tts_impl = DesktopTTS(ws_url=srv.url, voice="narrator")
    stream = tts_impl.stream()
    _, _, data = await _drive(stream, "First sentence here. Second sentence now.")

    assert srv.connections == 1
    assert srv.received == [
        {"text": "First sentence here.", "voice": "narrator"},
        {"text": "Second sentence now.", "voice": "narrator"},
    ]
    # Both PCM runs survive intact and in order (sentence-1 audio precedes
    # sentence-2 audio), recovered by stripping the trailing silence marker.
    assert data.rstrip(b"\x00") == _S1_PCM + _S2_PCM

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_streaming_pipelined_ordering_no_loss(tts_server_factory):
    # Pipelined ordering: sentence-2 PCM follows sentence-1 PCM with nothing lost
    # or interleaved. Distinct per-sentence payloads make any reorder/loss visible.
    # Each run is delivered as odd-length chunks to also exercise the within-message
    # 16-bit alignment carry (and confirm it does not leak across sentences).
    srv = await tts_server_factory(
        lambda i: _odd_chunks(_S1_PCM) if i == 0 else _odd_chunks(_S2_PCM), loop=True
    )

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.stream()
    _, _, data = await _drive(
        stream, "The first sentence is here. The second sentence is now."
    )

    real = data.rstrip(b"\x00")
    assert real == _S1_PCM + _S2_PCM
    # Sentence-1 audio is the exact prefix; sentence-2 audio is the exact suffix.
    assert real[: len(_S1_PCM)] == _S1_PCM
    assert real[len(_S1_PCM) :] == _S2_PCM

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_streaming_error_frame_raises_api_error(tts_server_factory):
    # An {"error": ...} frame from the server surfaces as an APIError. max_retry=0
    # so the single failure propagates instead of being retried.
    srv = await tts_server_factory([b"\x01\x02"], mode="error", error="kaboom",
                                   loop=True)

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.stream(conn_options=APIConnectOptions(max_retry=0))

    with pytest.raises(APIError):
        await _drive(stream, "fail please.")

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_streaming_bargein_drops_socket_then_reconnects(tts_server_factory):
    # Barge-in: cancel mid-synthesis → the persistent socket is dropped (the
    # server's handler `finally` fires), and a SUBSEQUENT stream() reconnects
    # (second connection accepted) and synthesizes cleanly.
    srv = await tts_server_factory([], mode="hang", loop=True)
    # First connection (the interrupted turn) streams one big chunk and then
    # hangs with NO terminal frame, so synthesis is genuinely mid-flight when we
    # cancel. The reconnected second connection streams small, recoverable
    # per-sentence PCM and completes normally.
    srv.chunks = lambda i: (
        [_BIG_CHUNK]
        if srv.connections == 1
        else ([_S1_PCM] if i == 0 else [_S2_PCM])
    )

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.stream()
    stream.push_text("interrupt me now.")
    stream.end_input()

    aiter = stream.__aiter__()
    # Pull exactly the first frame — synthesis is now genuinely mid-flight.
    first = await asyncio.wait_for(aiter.__anext__(), timeout=2)
    assert first.frame is not None
    assert srv.disconnected is False

    # Abort mid-stream (what a real barge-in does): aclose() cancels _run, whose
    # CancelledError path calls _drop_ws() → the socket closes.
    await asyncio.wait_for(stream.aclose(), timeout=2)
    await asyncio.wait_for(srv.disconnected_event.wait(), timeout=2)
    assert srv.disconnected is True
    assert srv.connections == 1

    # The next turn reconnects (second connection accepted) and completes cleanly.
    stream2 = tts_impl.stream()
    _, _, data = await asyncio.wait_for(
        _drive(stream2, "This is the first part. And here is the second part."),
        timeout=5,
    )
    assert srv.connections == 2
    assert data.rstrip(b"\x00") == _S1_PCM + _S2_PCM

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_streaming_disconnect_midstream_raises_api_error(tts_server_factory):
    # Mid-stream server disconnect on the persistent socket: the loop server
    # streams the first sentence's PCM then closes the socket WITHOUT the terminal
    # {"done": true}. The plugin's drain loop (`await ws.recv()`) then raises
    # ConnectionClosed, which `_run` surfaces as an APIError. max_retry=0 so the
    # single failure propagates instead of being retried.
    srv = await tts_server_factory([_S1_PCM], mode="disconnect", loop=True)

    tts_impl = DesktopTTS(ws_url=srv.url)
    stream = tts_impl.stream(conn_options=APIConnectOptions(max_retry=0))

    with pytest.raises(APIError):
        await _drive(stream, "drop me midstream.")

    # The server accepted exactly one connection (and then closed it itself).
    assert srv.connections == 1

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_streaming_midstream_flush_separate_sentences(tts_server_factory):
    # Mid-stream FlushSentinel: feed two sentences and a `flush()` within ONE
    # stream, exercising the `_feed` FlushSentinel branch (it forwards the flush
    # to the streaming tokenizer, forcing emission of the buffered sentences) and
    # the `_recv` re-check under partial sends. Both sentences must go out as
    # SEPARATE {"text","voice"} requests over the SAME connection, with PCM
    # emitted in order.
    #
    # Note: livekit-agents' SynthesizeStream allows only one segment per stream —
    # a `push_text` after a `flush()` is dropped with a deprecation warning — so
    # the two sentences are pushed (with a sentence boundary between them) before
    # a single `flush()` forces them out, rather than as two push/flush cycles.
    srv = await tts_server_factory(
        lambda i: [_S1_PCM] if i == 0 else [_S2_PCM], loop=True
    )

    tts_impl = DesktopTTS(ws_url=srv.url, voice="narrator")
    stream = tts_impl.stream()

    # The trailing space after the first sentence gives the streaming tokenizer a
    # boundary it actually splits on; `flush()` then forces both buffered
    # sentences to be emitted as distinct tokens.
    stream.push_text("First sentence here. ")
    stream.push_text("Second sentence now.")
    stream.flush()
    stream.end_input()

    frames = []
    async for ev in stream:
        frames.append(ev.frame)
    await stream.aclose()
    data = b"".join(bytes(f.data) for f in frames)

    # One persistent connection carried both sentences as separate requests.
    assert srv.connections == 1
    assert srv.received == [
        {"text": "First sentence here.", "voice": "narrator"},
        {"text": "Second sentence now.", "voice": "narrator"},
    ]
    # Both PCM runs survive intact and in order, recovered by stripping the
    # framework's trailing end-of-segment silence marker.
    assert data.rstrip(b"\x00") == _S1_PCM + _S2_PCM

    await tts_impl.aclose()
