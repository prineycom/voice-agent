"""Tests for the TTS WebSocket server (health + WS streaming protocol).

`synthesize.stream_pcm` is monkeypatched, so no GPU/model is needed.
"""

import json

import pytest
from fastapi.testclient import TestClient

import server
import synthesize


@pytest.fixture
def loaded():
    prev = server.state["loaded"]
    server.state["loaded"] = True
    yield
    server.state["loaded"] = prev


@pytest.fixture
def not_loaded():
    prev = server.state["loaded"]
    server.state["loaded"] = False
    yield
    server.state["loaded"] = prev


def test_health_degraded_when_not_loaded(not_loaded):
    client = TestClient(server.app)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["service"] == "tts"
    assert body["model_loaded"] is False


def test_health_ok_when_loaded(loaded, monkeypatch):
    # pin the engine so health fields are deterministic (custom_voice exposes `speaker`).
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    monkeypatch.setenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    monkeypatch.setenv("TTS_SPEAKER", "aiden")
    synthesize._engine = None  # force re-selection with the env above
    client = TestClient(server.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["engine"] == "custom_voice"
    assert body["model"] == "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    assert body["speaker"] == "aiden"
    synthesize._engine = None


def test_ws_rejects_when_not_loaded(not_loaded):
    client = TestClient(server.app)
    with client.websocket_connect("/tts") as ws:
        assert ws.receive_json() == {"error": "model not loaded"}


def test_ws_streams_chunks_then_done(loaded, monkeypatch):
    chunks = [b"\x01\x02", b"\x03\x04", b"\x05\x06"]

    def fake_stream(text, voice):
        assert text == "привет"
        for c in chunks:
            yield c

    monkeypatch.setattr(synthesize, "stream_pcm", fake_stream)
    client = TestClient(server.app)
    received = []
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "привет", "voice": "default"}))
        while True:
            msg = ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                received.append(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                assert json.loads(msg["text"]) == {"done": True}
                break
    assert received == chunks


def test_ws_empty_text_returns_error(loaded):
    client = TestClient(server.app)
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "   "}))
        assert ws.receive_json() == {"error": "empty text"}


def test_ws_producer_exception_is_reported(loaded, monkeypatch):
    def boom(text, voice):
        raise RuntimeError("synth failed")
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr(synthesize, "stream_pcm", boom)
    client = TestClient(server.app)
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "hi"}))
        msg = ws.receive_json()
        assert "synth failed" in msg["error"]


def test_ws_missing_text_key_returns_error(loaded):
    client = TestClient(server.app)
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"voice": "default"}))
        assert ws.receive_json() == {"error": "empty text"}
