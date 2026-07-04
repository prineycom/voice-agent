"""A2F WebSocket service — Audio2Face-3D → ARKit blendshapes (Epic 8, Phase 1).

Runs on the Desktop GPU next to STT/TTS. Consumes a TTS-audio utterance (+ the
emotion the LLM tagged) and emits ARKit blendshape frames that the agent forwards
to the browser on the `voiceagent` data channel (design decisions in issue #33 /
docs/research/2026-07-03-a2f-3d-spike.md). Built on a batch-1 TensorRT engine so
it coexists with STT+TTS (~0.3 GB VRAM, spike-measured).

WS /a2f:
    Client → Server (per utterance, in order):
        1. JSON {"emotion": "happy"}  or  {"emotion": [10 floats]}   (optional; default neutral)
        2. binary PCM16 @ 24 kHz mono frames (the forked TTS audio)
        3. JSON {"end": true}
    Server → Client:
        JSON {"type": "blendshapes", "frame": i, "t": sec, "arkit": {name: value, ...}} per frame,
        then JSON {"done": true}   (or {"error": "..."} on failure)

GET /health: backend/engine status, consistent with the STT/TTS services.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from emotion import enum_to_vector
from engine import FPS, make_backend

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("a2f")

HOST = os.getenv("A2F_HOST", "0.0.0.0")
PORT = int(os.getenv("A2F_PORT", "8003"))

backend = make_backend()


def _emotion_vector(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return enum_to_vector(value)
    if isinstance(value, list):
        return [float(x) for x in value]
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Cleanly stop the persistent A2F helper so no orphan GPU process outlives us.
    await backend.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse(
        {
            "status": "ok",
            "service": "a2f",
            "backend": backend.name,
            "device": "cuda" if backend.name == "helper" else "cpu",
            "fps": FPS,
            "model_loaded": backend.name != "helper",  # helper reports real load once wired
        },
        status_code=200,
    )


@app.websocket("/a2f")
async def a2f_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            emotion: list[float] | None = None
            pcm = bytearray()
            ended = False
            # Collect one utterance: optional emotion control, PCM frames, {"end"}.
            while not ended:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    pcm += msg["bytes"]
                elif msg.get("text") is not None:
                    ctrl = json.loads(msg["text"])
                    if "emotion" in ctrl:
                        emotion = _emotion_vector(ctrl["emotion"])
                    if ctrl.get("end"):
                        ended = True
            # Run inference and stream frames back.
            try:
                async for frame in backend.stream(bytes(pcm), emotion):
                    await ws.send_json({"type": "blendshapes", **frame})
                await ws.send_json({"done": True})
            except NotImplementedError as e:
                await ws.send_json({"error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("A2F inference error")
                await ws.send_json({"error": str(e)})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("A2F websocket error")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
