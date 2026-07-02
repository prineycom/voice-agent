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


def test_ws_streams_multiple_messages_on_one_connection(loaded, monkeypatch):
    # The streaming plugin reuses a single connection for sequential segments:
    # each text message must produce its own PCM chunks followed by {"done": True}.
    chunks_by_text = {
        "first": [b"\x01\x02", b"\x03\x04"],
        "second": [b"\x05\x06", b"\x07\x08", b"\x09\x0a"],
    }

    def fake_stream(text, voice):
        for c in chunks_by_text[text]:
            yield c

    monkeypatch.setattr(synthesize, "stream_pcm", fake_stream)
    client = TestClient(server.app)

    def collect_until_done(ws):
        received = []
        while True:
            msg = ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                received.append(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                assert json.loads(msg["text"]) == {"done": True}
                break
        return received

    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "first", "voice": "default"}))
        assert collect_until_done(ws) == chunks_by_text["first"]

        ws.send_text(json.dumps({"text": "second", "voice": "default"}))
        assert collect_until_done(ws) == chunks_by_text["second"]


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


# --- /unload and /reload endpoint tests --- #


@pytest.fixture
def engine_loaded(monkeypatch):
    """Load the (stubbed) TTS engine so unload/reload can operate on it."""
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    synthesize._engine = None
    synthesize.load_model()
    server.state["loaded"] = True
    yield
    synthesize._engine = None
    server.state["loaded"] = False


def test_unload_when_loaded(engine_loaded):
    client = TestClient(server.app)
    resp = client.post("/unload")
    assert resp.status_code == 200
    assert resp.json()["model_loaded"] is False
    assert server.state["loaded"] is False
    assert synthesize._engine._model is None


def test_unload_idempotent_when_already_unloaded(not_loaded):
    synthesize._engine = None
    client = TestClient(server.app)
    resp = client.post("/unload")
    assert resp.status_code == 200
    assert resp.json()["model_loaded"] is False


def test_reload_when_unloaded(not_loaded, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    synthesize._engine = None
    client = TestClient(server.app)
    resp = client.post("/reload")
    assert resp.status_code == 200
    assert resp.json()["model_loaded"] is True
    assert server.state["loaded"] is True
    assert synthesize.is_loaded() is True
    synthesize._engine = None
    server.state["loaded"] = False


def test_reload_idempotent_when_already_loaded(engine_loaded):
    client = TestClient(server.app)
    resp = client.post("/reload")
    assert resp.status_code == 200
    assert resp.json()["model_loaded"] is True


def test_unload_then_ws_rejects(engine_loaded):
    client = TestClient(server.app)
    assert client.post("/unload").status_code == 200
    with client.websocket_connect("/tts") as ws:
        assert ws.receive_json() == {"error": "model not loaded"}


def test_unload_reload_cycle(engine_loaded):
    client = TestClient(server.app)
    assert client.post("/unload").json()["model_loaded"] is False
    assert client.post("/reload").json()["model_loaded"] is True
    # WS accepts again after reload — sending empty text proves we're past the not-loaded gate
    with client.websocket_connect("/tts") as ws:
        ws.send_text(json.dumps({"text": "  "}))
        assert ws.receive_json() == {"error": "empty text"}
