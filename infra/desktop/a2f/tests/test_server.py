"""Contract tests for the A2F service (mock backend — no GPU needed)."""

import json
import os
import struct
import sys

os.environ.setdefault("A2F_BACKEND", "mock")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from arkit import ARKIT_52  # noqa: E402

client = TestClient(server.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "a2f"
    assert body["backend"] == "mock"
    assert body["fps"] == 30


def _sine_pcm(seconds=1.0, rate=24000, freq=180.0, amp=0.5):
    import math

    n = int(seconds * rate)
    return struct.pack(f"<{n}h", *[int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


def test_ws_emits_blendshape_frames():
    with client.websocket_connect("/a2f") as ws:
        ws.send_text(json.dumps({"emotion": "happy"}))
        ws.send_bytes(_sine_pcm(seconds=1.0))
        ws.send_text(json.dumps({"end": True}))
        frames = []
        while True:
            m = ws.receive_json()
            if m.get("done"):
                break
            assert m["type"] == "blendshapes"
            assert set(ARKIT_52).issubset(m["arkit"].keys())
            frames.append(m)
        # ~30 fps over ~1 s
        assert 25 <= len(frames) <= 35
        # happy emotion biases a smile
        assert frames[len(frames) // 2]["arkit"]["MouthSmileLeft"] > 0
        # jaw moves with the audio envelope
        assert max(f["arkit"]["JawOpen"] for f in frames) > 0.1
