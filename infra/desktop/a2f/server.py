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

Feeding is INCREMENTAL (#45): each PCM frame is forwarded to the backend as it
arrives (``stream_from``), so blendshape frames may reach the client BEFORE it
sends {"end"}. The wire protocol is unchanged — only the frames' timing.

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


_END = object()  # chunk-queue sentinel: the client sent {"end": true}


async def _queue_chunks(queue: asyncio.Queue):
    """Async chunk iterator over the receive loop's queue, closed by ``_END``."""
    while True:
        chunk = await queue.get()
        if chunk is _END:
            return
        yield chunk


async def _forward_frames(ws: WebSocket, queue: asyncio.Queue, emotion: list[float] | None) -> None:
    """Per-utterance forward task: stream frames from the backend to the client
    as PCM chunks arrive, then {"done"} (or {"error"}). The ONLY task that sends
    during an utterance, so frames and control replies never interleave."""
    gen = backend.stream_from(_queue_chunks(queue), emotion)
    try:
        try:
            async for frame in gen:
                await ws.send_json({"type": "blendshapes", **frame})
            await ws.send_json({"done": True})
        except NotImplementedError as e:
            await ws.send_json({"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("A2F inference error")
            await ws.send_json({"error": str(e)})
    finally:
        # Deterministic teardown when this task is cancelled (client gone
        # mid-utterance): closing the generator makes the engine abort the
        # utterance on the helper, which stays in service.
        await gen.aclose()


@app.websocket("/a2f")
async def a2f_ws(ws: WebSocket):
    await ws.accept()
    forward: asyncio.Task | None = None
    try:
        while True:
            # One utterance: optional emotion control, PCM frames, {"end"}.
            # Inference starts on the FIRST PCM frame; audio is fed to the
            # backend incrementally, so blendshape frames may go out while the
            # client is still sending audio.
            emotion: list[float] | None = None
            queue: asyncio.Queue = asyncio.Queue()
            ended = False
            while not ended:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    queue.put_nowait(msg["bytes"])
                    if forward is None:
                        forward = asyncio.create_task(_forward_frames(ws, queue, emotion))
                elif msg.get("text") is not None:
                    ctrl = json.loads(msg["text"])
                    if "emotion" in ctrl:
                        if forward is None:
                            emotion = _emotion_vector(ctrl["emotion"])
                        else:  # inference already runs with its emotion vector
                            log.warning("emotion control after audio started — ignored")
                    if ctrl.get("end"):
                        ended = True
            queue.put_nowait(_END)
            if forward is None:
                # Audio-less utterance (just {"end"}): still run the backend so
                # the client gets its {"done"} reply.
                forward = asyncio.create_task(_forward_frames(ws, queue, emotion))
            await forward
            forward = None
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("A2F websocket error")
    finally:
        if forward is not None:
            # Client disconnected (or the receive loop failed) mid-utterance:
            # abort the in-flight inference (the helper stays in service).
            forward.cancel()
            await asyncio.gather(forward, return_exceptions=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
