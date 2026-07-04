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
    received = []
    while True:
        msg = ws.receive()
        if "bytes" in msg and msg["bytes"] is not None:
            received.append(msg["bytes"])
        elif "text" in msg and msg["text"] is not None:
            assert json.loads(msg["text"]) == {"done": True}
            break
    return received


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
            received = _collect_until_done(ws)
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


def test_tts_isolated_when_a2f_down(loaded, monkeypatch):
    monkeypatch.setattr(synthesize, "stream_pcm", _fake_stream)
    # Nothing listens here — the fork must fail silently.
    monkeypatch.setenv("A2F_WS_URL", "ws://127.0.0.1:1/a2f")
    monkeypatch.setenv("A2F_FORK_ENABLED", "1")

    client = TestClient(server.app)
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "hi", "voice": "default", "emotion": "happy"}))
        received = _collect_until_done(ws)
    # Load-bearing guarantee: the client still gets every PCM chunk + done.
    assert received == CHUNKS
