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
import json

import pytest
import websockets
from livekit.agents import APIConnectOptions, APIError

from tts_plugin import FRAME_MAX_BYTES, NUM_CHANNELS, SAMPLE_RATE, DesktopTTS


def _publish_spy():
    """A synchronous publisher spy → (publish callable, list of decoded dicts).

    The plugin's ``voiceagent`` publisher is a plain ``bytes`` sink; the spy
    decodes each payload so tests can assert forwarded blendshape/`done` frames.
    """
    published = []

    def publish(payload):
        published.append(json.loads(payload.decode()))

    return publish, published


def _bs_frame(index, t):
    """A deterministic A2F blendshape frame with sentence-relative ``t``."""
    return {"type": "blendshapes", "frame": index, "t": t, "arkit": {"jawOpen": 0.5}}


def _publish_raw_spy():
    """Like ``_publish_spy`` but also keeps the raw bytes for size assertions."""
    raw = []
    decoded = []

    def publish(payload):
        raw.append(bytes(payload))
        decoded.append(json.loads(payload.decode()))

    return publish, raw, decoded


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

    # The server saw exactly the request the plugin promised (emotion defaults to
    # "neutral" when no emotion source is wired).
    assert srv.received == [
        {"text": "hello world", "voice": "narrator", "emotion": "neutral"}
    ]

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
        {"text": "first", "voice": "default", "emotion": "neutral"},
        {"text": "second", "voice": "default", "emotion": "neutral"},
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
        {"text": "First sentence here.", "voice": "narrator", "emotion": "neutral"},
        {"text": "Second sentence now.", "voice": "narrator", "emotion": "neutral"},
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
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)
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

    # Barge-in emits exactly one reply-level {"done": true} on the voiceagent
    # channel (so the browser ends this reply's A2F stream) and nothing else.
    assert published == [{"done": True}]

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
        {"text": "First sentence here.", "voice": "narrator", "emotion": "neutral"},
        {"text": "Second sentence now.", "voice": "narrator", "emotion": "neutral"},
    ]
    # Both PCM runs survive intact and in order, recovered by stripping the
    # framework's trailing end-of-segment silence marker.
    assert data.rstrip(b"\x00") == _S1_PCM + _S2_PCM

    await tts_impl.aclose()


# --- A2F demux: emotion routing, blendshape re-basing, reply-level done ------
#
# The Desktop reply now interleaves, per sentence: PCM, blendshape frames, the
# audio {"done"}, then one {"a2f_done"}. The plugin forwards re-based blendshapes
# on the `voiceagent` channel, threads the current emotion into each request, and
# emits exactly ONE reply-level {"done": true} — never leaking the internal
# audio-`done`/`a2f_done` markers.


@pytest.mark.asyncio
async def test_emotion_threaded_per_sentence(tts_server_factory):
    # The emotion getter is read once per sentence AT FLUSH TIME, so consecutive
    # sentences in one reply can carry different emotions (latest-before-flush wins).
    srv = await tts_server_factory(
        lambda i: [_S1_PCM] if i == 0 else [_S2_PCM], loop=True
    )
    tts_impl = DesktopTTS(ws_url=srv.url)

    emotions = iter(["happy", "sad"])
    tts_impl.set_emotion_source(lambda: next(emotions, "neutral"))

    await _drive(tts_impl.stream(), "First sentence here. Second sentence now.")
    assert [r["emotion"] for r in srv.received] == ["happy", "sad"]

    # Unknown/falsy values clamp to the default emotion.
    tts_impl.set_emotion_source(lambda: "ecstatic")
    srv.received.clear()
    await _drive(tts_impl.stream(), "Another one here.")
    assert srv.received[0]["emotion"] == "neutral"

    await tts_impl.aclose()

    # With no source wired at all, requests still default to neutral.
    srv2 = await tts_server_factory(lambda i: [_S1_PCM], loop=True)
    plain = DesktopTTS(ws_url=srv2.url)
    await _drive(plain.stream(), "No source here.")
    assert srv2.received[0]["emotion"] == "neutral"
    await plain.aclose()


@pytest.mark.asyncio
async def test_blendshape_t_rebased_monotonic_across_reply(tts_server_factory):
    # Each sentence's PCM is 4800 bytes = 2400 samples @24kHz = exactly 0.1s of
    # audio, so sentence-2's frames must be offset by 0.1s from their sentence-
    # relative t (which stays below the 0.1s duration to keep `t` monotonic).
    pcm = b"\x33" * 4800
    s1_frames = [_bs_frame(0, 0.0), _bs_frame(1, 0.04), _bs_frame(2, 0.08)]
    s2_frames = [_bs_frame(0, 0.0), _bs_frame(1, 0.04), _bs_frame(2, 0.08)]
    srv = await tts_server_factory(
        lambda i: [pcm],
        loop=True,
        blendshapes=lambda i: s1_frames if i == 0 else s2_frames,
    )

    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here. Second sentence now.")

    forwarded = [p for p in published if p.get("type") == "blendshapes"]
    ts = [p["t"] for p in forwarded]
    # Two sentences × three frames, forwarded in order.
    assert len(forwarded) == 6
    # Reply-relative t is non-decreasing across the sentence boundary.
    assert ts == sorted(ts)
    # Sentence-1 keeps its sentence-relative t (offset 0); sentence-2 is offset by
    # sentence-1's audio duration (0.1s).
    assert ts[:3] == pytest.approx([0.0, 0.04, 0.08])
    assert ts[3:] == pytest.approx([0.1, 0.14, 0.18])
    # Frame index is forwarded untouched; arkit carries the significant keys,
    # rounded to 3 decimals and capped to FRAME_MAX_BYTES — {"jawOpen": 0.5}
    # survives the rounding intact.
    assert [p["frame"] for p in forwarded] == [0, 1, 2, 0, 1, 2]
    assert all(p["arkit"] == {"jawOpen": 0.5} for p in forwarded)

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_blendshape_t_rebased_under_out_of_order_a2f_done(tts_server_factory):
    # Out-of-order background A2F fork: sentence-1's a2f_done arrives AFTER
    # sentence-2's audio {"done"}, then sentence-2's frames follow. The plugin must
    # pair each a2f_done with ITS OWN sentence's finalized duration (via the FIFO),
    # not the last-finalized one, so sentence-2's frames are offset by sentence-1's
    # duration — never skewed by sentence-2's own (different) duration.
    pcm1 = b"\x33" * 4800  # 2400 samples @24kHz = exactly 0.1s
    pcm2 = b"\x33" * 9600  # 4800 samples @24kHz = exactly 0.2s (distinct from d1)
    s1_frames = [_bs_frame(0, 0.0), _bs_frame(1, 0.04), _bs_frame(2, 0.08)]
    s2_frames = [_bs_frame(0, 0.0), _bs_frame(1, 0.05), _bs_frame(2, 0.1)]
    srv = await tts_server_factory(
        lambda i: [pcm1] if i == 0 else [pcm2],
        loop=True,
        blendshapes=lambda i: s1_frames if i == 0 else s2_frames,
        a2f_reorder=True,
    )

    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here. Second sentence now.")

    forwarded = [p for p in published if p.get("type") == "blendshapes"]
    ts = [p["t"] for p in forwarded]
    assert len(forwarded) == 6
    # Monotonic across the sentence boundary despite the reordered a2f_done.
    assert ts == sorted(ts)
    # Sentence-1 keeps offset 0; sentence-2 is offset by sentence-1's 0.1s duration
    # (NOT by sentence-2's own 0.2s — that skew is exactly the fixed bug).
    assert ts[:3] == pytest.approx([0.0, 0.04, 0.08])
    assert ts[3:] == pytest.approx([0.1, 0.15, 0.2])
    # Exactly one reply-level done; no internal markers leak.
    assert published.count({"done": True}) == 1
    assert not any(p.get("type") == "a2f_done" for p in published)

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_malformed_blendshape_frame_never_breaks_audio(tts_server_factory):
    # A2F/facial data must NEVER break audio: a blendshape frame missing a key (or
    # carrying a None `t`) must be dropped harmlessly — the reply's AUDIO still
    # completes and exactly one reply-level {"done": true} is still published (no
    # _drop_ws, no APIError propagating out of _drive).
    malformed = [
        {"type": "blendshapes", "frame": 0, "arkit": {"jawOpen": 0.5}},  # no "t"
        {"type": "blendshapes", "frame": 1, "t": None, "arkit": {}},  # None t
        {"type": "blendshapes", "frame": 2, "t": 0.0},  # no "arkit"
    ]
    srv = await tts_server_factory(
        lambda i: [_S1_PCM] if i == 0 else [_S2_PCM],
        loop=True,
        blendshapes=lambda i: malformed,
    )
    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    _, _, data = await _drive(
        tts_impl.stream(), "First sentence here. Second sentence now."
    )

    # Audio completed intact and in order despite every A2F frame being malformed.
    assert data.rstrip(b"\x00") == _S1_PCM + _S2_PCM
    # The malformed frames were dropped (never forwarded), and exactly one reply
    # done still reached the channel — audio path unbroken.
    assert not any(p.get("type") == "blendshapes" for p in published)
    assert published.count({"done": True}) == 1

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_dense_blendshape_frame_capped_to_frame_max_bytes(tts_server_factory):
    # A dense (68-key) A2F frame must serialize within FRAME_MAX_BYTES — one MTU
    # on the lossy channel. The highest-|value| keys win, with equal values
    # tie-broken by name, so the cut falls on the alphabetical tail of the tied
    # 0.5-filler block while the few larger values always survive.
    fillers = {f"filler{i:02d}LongBlendshapeName": 0.5 for i in range(66)}
    dense = {**fillers, "zzBig": 0.9, "zzBigger": 0.95}
    frame = {"type": "blendshapes", "frame": 0, "t": 0.0, "arkit": dense}
    srv = await tts_server_factory(
        lambda i: [_S1_PCM], loop=True, blendshapes=lambda i: [frame]
    )
    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, raw, decoded = _publish_raw_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here.")

    frames = [(r, p) for r, p in zip(raw, decoded) if p.get("type") == "blendshapes"]
    assert len(frames) == 1
    raw_frame, forwarded = frames[0]
    # The full frame would blow the budget; the published payload fits it.
    assert len(json.dumps(frame, separators=(",", ":")).encode()) > FRAME_MAX_BYTES
    assert len(raw_frame) <= FRAME_MAX_BYTES
    arkit = forwarded["arkit"]
    # The largest values survive even though they sort LAST by name...
    assert arkit["zzBigger"] == 0.95
    assert arkit["zzBig"] == 0.9
    # ...and the tied 0.5 fillers are kept as an alphabetical PREFIX (name-order
    # tiebreak): the dropped keys are exactly the alphabetical tail.
    kept_fillers = sorted(k for k in arkit if k in fillers)
    assert 0 < len(kept_fillers) < len(fillers)
    assert kept_fillers == sorted(fillers)[: len(kept_fillers)]
    assert all(arkit[k] == 0.5 for k in kept_fillers)

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_sparse_blendshape_frame_passes_with_rounding_only(tts_server_factory):
    # A typical sparse frame is far under the budget: every significant key passes
    # through, with values (and the re-based `t`) rounded to 3 decimals.
    frame = {
        "type": "blendshapes",
        "frame": 3,
        "t": 0.0333333,
        "arkit": {"jawOpen": 0.53001, "eyeBlinkLeft": 0.1239, "browDownLeft": 1.0},
    }
    srv = await tts_server_factory(
        lambda i: [_S1_PCM], loop=True, blendshapes=lambda i: [frame]
    )
    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here.")

    forwarded = [p for p in published if p.get("type") == "blendshapes"]
    assert len(forwarded) == 1
    assert forwarded[0]["frame"] == 3
    assert forwarded[0]["t"] == 0.033
    assert forwarded[0]["arkit"] == {
        "jawOpen": 0.53,
        "eyeBlinkLeft": 0.124,
        "browDownLeft": 1.0,
    }

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_blendshape_keys_rounding_to_zero_are_dropped(tts_server_factory):
    # Keys whose value rounds to 0 at 3 decimals are absent from the payload —
    # the frontend reads a missing key as 0 (arkit-map.js), so this is lossless
    # there and buys back budget on the lossy channel.
    frame = {
        "type": "blendshapes",
        "frame": 0,
        "t": 0.0,
        "arkit": {
            "jawOpen": 0.5,
            "browDownLeft": 0.0004,
            "cheekPuff": 0.0,
            "noseSneerLeft": -0.0002,
        },
    }
    srv = await tts_server_factory(
        lambda i: [_S1_PCM], loop=True, blendshapes=lambda i: [frame]
    )
    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here.")

    forwarded = [p for p in published if p.get("type") == "blendshapes"]
    assert len(forwarded) == 1
    assert forwarded[0]["arkit"] == {"jawOpen": 0.5}

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_blendshape_payload_uses_compact_separators(tts_server_factory):
    # Wire bytes carry no separator whitespace — every byte counts against the
    # single-MTU budget, so the payload is dumped with compact separators.
    srv = await tts_server_factory(
        lambda i: [_S1_PCM], loop=True, blendshapes=lambda i: [_bs_frame(0, 0.0)]
    )
    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, raw, decoded = _publish_raw_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here.")

    raw_frames = [
        r for r, p in zip(raw, decoded) if p.get("type") == "blendshapes"
    ]
    assert len(raw_frames) == 1
    assert b" " not in raw_frames[0]

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_exactly_one_reply_done_per_reply(tts_server_factory):
    # Two sentences → two per-sentence a2f_done markers server-side, but the browser
    # must see exactly ONE reply-level {"done": true} (not one per sentence), and
    # the internal audio-`done`/`a2f_done` markers are never forwarded.
    srv = await tts_server_factory(
        lambda i: [_S1_PCM] if i == 0 else [_S2_PCM],
        loop=True,
        blendshapes=lambda i: [_bs_frame(0, 0.0)],
    )
    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    await _drive(tts_impl.stream(), "First sentence here. Second sentence now.")

    assert published.count({"done": True}) == 1
    assert not any(p.get("type") == "a2f_done" for p in published)
    # Only blendshapes and the single reply done ever reach the channel.
    assert all(
        p == {"done": True} or p.get("type") == "blendshapes" for p in published
    )

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_publish_failure_never_blocks_audio(tts_server_factory):
    # A publisher that raises on every call must never stop PCM from being emitted:
    # blendshape forwarding (and the reply done) are lossy fire-and-forget.
    srv = await tts_server_factory(
        lambda i: [_S1_PCM] if i == 0 else [_S2_PCM],
        loop=True,
        blendshapes=lambda i: [_bs_frame(0, 0.0), _bs_frame(1, 0.01)],
    )
    tts_impl = DesktopTTS(ws_url=srv.url)

    def boom(_payload):
        raise RuntimeError("publish exploded")

    tts_impl.set_publisher(boom)

    _, _, data = await _drive(
        tts_impl.stream(), "First sentence here. Second sentence now."
    )
    # Every raising publish is swallowed; audio is emitted intact and in order.
    assert data.rstrip(b"\x00") == _S1_PCM + _S2_PCM

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_a2f_absent_audio_and_bargein_unaffected(tts_server_factory):
    # Total A2F outage: the server sends PCM + audio done but NO blendshapes and NO
    # a2f_done marker. The audio path and barge-in must be completely unaffected —
    # audio still flows, and barge-in still emits exactly one reply-level done.
    srv = await tts_server_factory([], mode="hang", loop=True, a2f=False)
    srv.chunks = lambda i: [_BIG_CHUNK]

    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    stream = tts_impl.stream()
    stream.push_text("no a2f here.")
    stream.end_input()

    aiter = stream.__aiter__()
    # Audio still flows even with A2F entirely absent.
    first = await asyncio.wait_for(aiter.__anext__(), timeout=2)
    assert first.frame is not None
    assert srv.disconnected is False

    # Barge-in still works: the socket is dropped and exactly one reply-level
    # {"done": true} is emitted, with no blendshapes ever forwarded.
    await asyncio.wait_for(stream.aclose(), timeout=2)
    await asyncio.wait_for(srv.disconnected_event.wait(), timeout=2)
    assert srv.disconnected is True
    assert published == [{"done": True}]

    await tts_impl.aclose()


@pytest.mark.asyncio
async def test_bargein_stops_forwarding_and_emits_done(tts_server_factory):
    # Barge-in mid-forwarding: the server streams a big PCM chunk plus blendshape
    # frames, then hangs. On cancel, forwarding stops and exactly one reply-level
    # {"done": true} is emitted as the LAST frame on the channel.
    srv = await tts_server_factory(
        [], mode="hang", loop=True, blendshapes=lambda i: [_bs_frame(0, 0.0)]
    )
    srv.chunks = lambda i: [_BIG_CHUNK]

    tts_impl = DesktopTTS(ws_url=srv.url)
    publish, published = _publish_spy()
    tts_impl.set_publisher(publish)

    stream = tts_impl.stream()
    stream.push_text("interrupt me now.")
    stream.end_input()

    aiter = stream.__aiter__()
    first = await asyncio.wait_for(aiter.__anext__(), timeout=2)
    assert first.frame is not None

    # Barge-in mid-synthesis.
    await asyncio.wait_for(stream.aclose(), timeout=2)
    await asyncio.wait_for(srv.disconnected_event.wait(), timeout=2)

    # Exactly one reply-level done, and it is the LAST thing published — forwarding
    # stops at the barge-in. The internal a2f_done marker never leaks.
    assert published.count({"done": True}) == 1
    assert published[-1] == {"done": True}
    assert not any(p.get("type") == "a2f_done" for p in published)

    await tts_impl.aclose()
