"""Behavioral tests for DesktopSTT against a real fake /stt WebSocket server.

No GPU, no SFU, no silero — a `websockets.serve` server on localhost speaks the
Desktop /stt protocol (binary PCM16 in, JSON control + transcript out) and we
drive the real livekit-agents 1.6.x non-streaming `recognize()` path.

The key DD-2 guard is `test_one_socket_across_two_turns`: a single DesktopSTT
instance must reuse ONE persistent connection across sequential turns
(ADR-0006), so the server's connection counter stays at 1.
"""

import asyncio
import struct

import pytest
from livekit import rtc
from livekit.agents import APIConnectOptions, APIError, stt

from stt_plugin import NUM_CHANNELS, SAMPLE_RATE, DesktopSTT

# Single attempt: surface the original APIError immediately (no retry/backoff).
NO_RETRY = APIConnectOptions(max_retry=0)


def _make_frame(num_samples: int, *, sample_rate: int = SAMPLE_RATE) -> rtc.AudioFrame:
    """Build a mono PCM16 AudioFrame of `num_samples` ascending samples."""
    samples = list(range(num_samples))
    data = struct.pack("<%dh" % num_samples, *samples)
    return rtc.AudioFrame(
        data=data,
        sample_rate=sample_rate,
        num_channels=NUM_CHANNELS,
        samples_per_channel=num_samples,
    )


@pytest.mark.asyncio
async def test_single_turn_final_transcript(stt_server_factory):
    srv = await stt_server_factory(transcript="привет мир")
    num_samples = 320  # 20ms @16kHz
    frame = _make_frame(num_samples)

    stt_impl = DesktopSTT(ws_url=srv.url)
    event = await stt_impl.recognize(buffer=frame)

    assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "привет мир"
    assert event.alternatives[0].language == "ru"

    # The server received well-formed PCM16 for exactly this turn and the
    # terminal {"event": "end"} control frame.
    assert srv.received_bytes == num_samples * 2
    assert b"".join(srv.received_pcm) == frame.data.cast("b").tobytes()
    assert {"event": "end"} in srv.events

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_wake_gate_dormant_skips_desktop(stt_server_factory):
    """Wake-word gate: while Dormant the plugin returns empty WITHOUT connecting.

    This is the "don't pay for GPU STT while Dormant" guarantee (ADR-0021): the
    server must never see a connection for a gated-off turn.
    """
    srv = await stt_server_factory(transcript="привет мир")
    frame = _make_frame(320)

    stt_impl = DesktopSTT(ws_url=srv.url, gate=lambda: False)
    event = await stt_impl.recognize(buffer=frame)

    assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == ""  # empty → no LLM turn
    assert srv.connections == 0  # never touched the Desktop
    assert srv.received_bytes == 0

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_wake_gate_active_transcribes(stt_server_factory):
    """Gate open (Active) → normal transcription against the Desktop."""
    srv = await stt_server_factory(transcript="привет мир")
    stt_impl = DesktopSTT(ws_url=srv.url, gate=lambda: True)
    event = await stt_impl.recognize(buffer=_make_frame(320))
    assert event.alternatives[0].text == "привет мир"
    assert srv.connections == 1
    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_wake_transcript_filter_strips_wake_word(stt_server_factory):
    """The transcript_filter transforms the final text (wake-word strip)."""
    srv = await stt_server_factory(transcript="Приней сколько времени")
    stt_impl = DesktopSTT(
        ws_url=srv.url,
        transcript_filter=lambda t: t.replace("Приней ", ""),
    )
    event = await stt_impl.recognize(buffer=_make_frame(320))
    assert event.alternatives[0].text == "сколько времени"
    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_resamples_48k_to_16k(stt_server_factory):
    srv = await stt_server_factory()
    # 480 samples @48kHz (10ms) downsample to 160 samples @16kHz → 320 bytes.
    num_samples_48k = 480
    frame = _make_frame(num_samples_48k, sample_rate=48000)
    expected_16k_bytes = (num_samples_48k // 3) * 2  # 16000/48000 == 1/3

    stt_impl = DesktopSTT(ws_url=srv.url)
    event = await stt_impl.recognize(buffer=frame)

    assert event.alternatives[0].text == srv.transcript
    # The server saw the 16kHz-downsampled byte count, not the raw 48kHz bytes.
    assert srv.received_bytes == expected_16k_bytes
    assert srv.received_bytes == num_samples_48k * 2 // 3

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_error_frame_raises_api_error(stt_server_factory):
    srv = await stt_server_factory(mode="error", error="kaboom")
    frame = _make_frame(160)

    stt_impl = DesktopSTT(ws_url=srv.url)

    with pytest.raises(APIError):
        await stt_impl.recognize(buffer=frame, conn_options=NO_RETRY)

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_reset_sent_on_failure(stt_server_factory):
    # On an error turn the plugin sends a best-effort {"event": "reset"} so no
    # half-buffer leaks into the next turn; the socket survives the error frame,
    # so the server records the reset event.
    srv = await stt_server_factory(mode="error")
    frame = _make_frame(160)

    stt_impl = DesktopSTT(ws_url=srv.url)

    with pytest.raises(APIError):
        await stt_impl.recognize(buffer=frame, conn_options=NO_RETRY)

    # Let the reset frame land on the server before asserting.
    await asyncio.sleep(0.05)
    assert {"event": "reset"} in srv.events

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_one_socket_across_two_turns(stt_server_factory):
    # DD-2 guard: two sequential recognitions on the SAME instance must reuse a
    # single persistent connection (ADR-0006).
    srv = await stt_server_factory(transcript="один два")
    frame = _make_frame(160)

    stt_impl = DesktopSTT(ws_url=srv.url)

    ev1 = await stt_impl.recognize(buffer=frame)
    ev2 = await stt_impl.recognize(buffer=frame)

    assert ev1.alternatives[0].text == "один два"
    assert ev2.alternatives[0].text == "один два"
    assert srv.connections == 1
    # Both turns were flushed with their own end event over the one socket.
    assert sum(1 for e in srv.events if e.get("event") == "end") == 2

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_disconnect_raises_then_recovers(stt_server_factory):
    # Mid-turn server disconnect: the server closes the socket after {"event":
    # "end"}, so the plugin's recv() raises ConnectionClosed → generic-exception
    # path → best-effort reset (which fails on the dead socket and drops it) →
    # re-raised as APIError. A subsequent turn on the SAME instance reconnects
    # cleanly and succeeds.
    srv = await stt_server_factory(mode="disconnect", transcript="привет мир")
    frame = _make_frame(160)

    stt_impl = DesktopSTT(ws_url=srv.url)

    with pytest.raises(APIError):
        await stt_impl.recognize(buffer=frame, conn_options=NO_RETRY)

    # The dead socket was dropped, so the next turn is forced to reconnect.
    assert stt_impl._ws is None

    # The server then switches to a normal final-transcript turn for the recovery.
    srv.mode = "final"
    event = await stt_impl.recognize(buffer=frame)

    assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "привет мир"
    # Clean recovery means a fresh connection was opened for the second turn.
    assert srv.connections == 2

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_interim_partials_skipped(stt_server_factory):
    # The server emits interim {"is_final": false, "partial": ...} frames before
    # the final transcript; the plugin must skip them and return only the final.
    srv = await stt_server_factory(transcript="привет", partials=["при", "приве"])
    frame = _make_frame(160)

    stt_impl = DesktopSTT(ws_url=srv.url)
    event = await stt_impl.recognize(buffer=frame)

    assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "привет"

    await stt_impl.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_socket(stt_server_factory):
    srv = await stt_server_factory()
    frame = _make_frame(160)

    stt_impl = DesktopSTT(ws_url=srv.url)
    await stt_impl.recognize(buffer=frame)

    ws = stt_impl._ws
    assert ws is not None and ws.state.name == "OPEN"

    await stt_impl.aclose()

    # The persistent socket is dropped and closed.
    assert stt_impl._ws is None
    assert ws.state.name == "CLOSED"
