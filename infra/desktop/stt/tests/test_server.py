"""Tests for the STT WebSocket server (health, transcribe glue, WS protocol).

The real Whisper model is never loaded; `server.state` is driven directly and a
fake model stands in for `WhisperModel`, so these run on CPU without CUDA.
"""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server


class _Segment:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """Records the last transcribe call and returns canned segments."""

    def __init__(self, segments=("  hello world  ",), beam_calls=None):
        self._segments = [_Segment(t) for t in segments]
        self.calls = []

    def transcribe(self, samples, **kwargs):
        self.calls.append({"n": len(samples), **kwargs})
        return iter(self._segments), {"info": True}


@pytest.fixture
def loaded_model():
    """Install a fake loaded model and restore server state afterwards."""
    prev = dict(server.state)
    model = _FakeModel()
    server.state["model"] = model
    server.state["loaded"] = True
    yield model
    server.state.clear()
    server.state.update(prev)


@pytest.fixture
def not_loaded():
    prev = dict(server.state)
    server.state["model"] = None
    server.state["loaded"] = False
    yield
    server.state.clear()
    server.state.update(prev)


def _pcm(n_samples, value=1000):
    return np.full(n_samples, value, dtype="<i2").tobytes()


def test_health_degraded_when_not_loaded(not_loaded):
    client = TestClient(server.app)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["service"] == "stt"
    assert body["model_loaded"] is False


def test_health_ok_when_loaded(loaded_model):
    client = TestClient(server.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model"] == server.MODEL_NAME


def test_transcribe_joins_and_strips_segments(loaded_model):
    loaded_model._segments = [_Segment("Привет "), _Segment("мир")]
    out = server._transcribe(np.zeros(10, dtype=np.float32), final=True)
    assert out == "Привет мир"
    # final=True -> beam_size 5; final=False -> beam_size 1
    assert loaded_model.calls[-1]["beam_size"] == 5
    assert loaded_model.calls[-1]["language"] == server.LANGUAGE
    assert loaded_model.calls[-1]["vad_filter"] is True


def test_transcribe_beam_size_for_partial(loaded_model):
    server._transcribe(np.zeros(10, dtype=np.float32), final=False)
    assert loaded_model.calls[-1]["beam_size"] == 1


def test_ws_rejects_when_model_not_loaded(not_loaded):
    client = TestClient(server.app)
    with client.websocket_connect("/stt") as ws:
        msg = ws.receive_json()
        assert msg == {"error": "model not loaded"}


def test_ws_end_event_returns_final_transcript(loaded_model):
    client = TestClient(server.app)
    with client.websocket_connect("/stt") as ws:
        ws.send_bytes(_pcm(100))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
    assert msg["is_final"] is True
    assert msg["text"] == "hello world"
    assert msg["partial"] == ""


def test_ws_partial_emitted_after_threshold(loaded_model):
    client = TestClient(server.app)
    # threshold bytes = PARTIAL_INTERVAL_SEC * 16000 * 2
    threshold = int(server.PARTIAL_INTERVAL_SEC * server.SAMPLE_RATE * 2)
    with client.websocket_connect("/stt") as ws:
        ws.send_bytes(_pcm(threshold // 2 + 10))  # > threshold bytes
        msg = ws.receive_json()
    assert msg["is_final"] is False
    assert msg["text"] == "hello world"
    assert msg["partial"] == "hello world"


def test_ws_reset_clears_buffer(loaded_model):
    client = TestClient(server.app)
    with client.websocket_connect("/stt") as ws:
        ws.send_bytes(_pcm(50))
        ws.send_text(json.dumps({"event": "reset"}))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
    # buffer was cleared on reset, so the final transcribe ran on empty audio
    assert msg["is_final"] is True
    assert loaded_model.calls[-1]["n"] == 0


def test_ws_reports_error_and_keeps_serving(loaded_model):
    client = TestClient(server.app)

    def boom(*a, **k):
        raise RuntimeError("gpu exploded")

    loaded_model.transcribe = boom
    with client.websocket_connect("/stt") as ws:
        ws.send_bytes(_pcm(50))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert "gpu exploded" in msg["error"]
        # connection still alive: a reset is accepted without error
        ws.send_text(json.dumps({"event": "reset"}))
