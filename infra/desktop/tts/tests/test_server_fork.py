"""Integration tests: the /tts handler forks PCM + emotion into A2F, and stays
fully isolated from A2F failures.

Reuses the test_server.py fixture style: state["loaded"]=True + a monkeypatched
synthesize.stream_pcm that yields fixed PCM chunks (no GPU). A2F_WS_URL points at
the in-process fake for the forward test, and at a dead port for the isolation test.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from fake_a2f import FakeA2F

import server
import synthesize

CHUNKS = [b"\x01\x02", b"\x03\x04", b"\x05\x06"]


@pytest.fixture
def loaded():
    prev = server.state["loaded"]
    server.state["loaded"] = True
    yield
    server.state["loaded"] = prev


def _fake_stream(text, voice):
    for c in CHUNKS:
        yield c


def _collect_until_done(ws):
    """Read the audio stream up to the terminal audio {"done": true}.

    The /tts stream now multiplexes A2F frames onto the same socket, so tolerate
    interleaved {"type": "blendshapes"} / {"type": "a2f_done"} text frames — bucket
    them separately — and break on the audio {"done": true}.
    Returns (pcm_chunks, blendshapes, a2f_dones).
    """
    received = []
    blendshapes = []
    a2f_dones = []
    while True:
        msg = ws.receive()
        if "bytes" in msg and msg["bytes"] is not None:
            received.append(msg["bytes"])
            continue
        if "text" in msg and msg["text"] is not None:
            data = json.loads(msg["text"])
            if data == {"done": True}:
                break
            if data.get("type") == "blendshapes":
                blendshapes.append(data)
            elif data.get("type") == "a2f_done":
                a2f_dones.append(data)
    return received, blendshapes, a2f_dones


def _read_until_a2f_done(ws):
    """Read the frames that follow the audio {"done": true} up to (and including)
    the sentence's a2f_done marker, bucketing any blendshape frames.

    A2F infers on the full sentence PCM, so its blendshape frames and the single
    a2f_done marker arrive after the audio done. Returns (blendshapes, a2f_count).
    """
    blendshapes = []
    a2f_count = 0
    while True:
        msg = ws.receive()
        if "text" in msg and msg["text"] is not None:
            data = json.loads(msg["text"])
            if data.get("type") == "blendshapes":
                blendshapes.append(data)
            elif data.get("type") == "a2f_done":
                a2f_count += 1
                break
    return blendshapes, a2f_count


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_tts_forwards_emotion_and_pcm_to_a2f(loaded, monkeypatch):
    monkeypatch.setattr(synthesize, "stream_pcm", _fake_stream)
    fake = FakeA2F().start()
    monkeypatch.setenv("A2F_WS_URL", fake.url)
    monkeypatch.setenv("A2F_FORK_ENABLED", "1")

    client = TestClient(server.app)
    try:
        with client.websocket_connect("/tts") as ws:
            ws.send_text(json.dumps({"text": "hi", "voice": "default", "emotion": "happy"}))
            received, _, _ = _collect_until_done(ws)
        # Client got the full, unaffected PCM stream.
        assert received == CHUNKS
        # The background fork flushes emotion + the same PCM + end into A2F.
        assert _wait_for(lambda: any(e == ("end", True) for e in list(fake.events.queue)))
        events = fake.drain_events()
    finally:
        fake.stop()

    assert ("emotion", "happy") in events
    pcm = [v for (tag, v) in events if tag == "pcm"]
    assert pcm == CHUNKS
    tags = [t for (t, _) in events]
    assert tags.index("emotion") < tags.index("pcm") < tags.index("end")


def test_tts_forwards_blendshapes_and_a2f_done(loaded, monkeypatch):
    monkeypatch.setattr(synthesize, "stream_pcm", _fake_stream)
    fake = FakeA2F().start()
    monkeypatch.setenv("A2F_WS_URL", fake.url)
    monkeypatch.setenv("A2F_FORK_ENABLED", "1")

    client = TestClient(server.app)
    try:
        with client.websocket_connect("/tts") as ws:
            ws.send_text(json.dumps({"text": "hi", "voice": "default", "emotion": "happy"}))
            received, pre_bs, pre_a2f = _collect_until_done(ws)
            # A2F infers on the full sentence, so its frames + a2f_done follow the
            # audio done; read on to the terminal a2f_done marker.
            post_bs, a2f_count = _read_until_a2f_done(ws)
    finally:
        fake.stop()

    # Audio path unaffected: every PCM chunk arrives before the audio done, with
    # no A2F frame preceding it.
    assert received == CHUNKS
    assert pre_bs == [] and pre_a2f == []
    # The fork forwards FakeA2F's 2 blendshape frames verbatim, then exactly one
    # a2f_done closes the sentence.
    assert post_bs == [
        {"type": "blendshapes", "frame": 0, "t": 0.0, "arkit": {"JawOpen": 0.1, "MouthSmileLeft": 0.2}},
        {"type": "blendshapes", "frame": 1, "t": 1 / 30.0, "arkit": {"JawOpen": 0.1, "MouthSmileLeft": 0.2}},
    ]
    assert a2f_count == 1


def test_tts_isolated_when_a2f_down(loaded, monkeypatch):
    monkeypatch.setattr(synthesize, "stream_pcm", _fake_stream)
    # Nothing listens here — the fork must fail silently.
    monkeypatch.setenv("A2F_WS_URL", "ws://127.0.0.1:1/a2f")
    monkeypatch.setenv("A2F_FORK_ENABLED", "1")

    client = TestClient(server.app)
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "hi", "voice": "default", "emotion": "happy"}))
        received, blendshapes, a2f_dones = _collect_until_done(ws)
        # The failed fork still signals end-of-A2F once, after the audio done.
        post_bs, a2f_count = _read_until_a2f_done(ws)
    # Load-bearing guarantee: the client still gets every PCM chunk + done.
    assert received == CHUNKS
    # A2F down → no blendshapes, but exactly one a2f_done per sentence.
    assert blendshapes == [] and post_bs == []
    assert a2f_dones == [] and a2f_count == 1
