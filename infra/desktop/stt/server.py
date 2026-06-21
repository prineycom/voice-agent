"""STT WebSocket service — faster-whisper large-v3-turbo.

WS /stt:  client streams binary 16-bit PCM @ 16kHz mono; JSON control
          {"event":"end"} flushes a final transcript, {"event":"reset"} clears.
          Server emits {"text","is_final","partial"}.
GET /health: model/load status.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel

from audio import pcm16_to_float32

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stt")

MODEL_NAME = os.getenv("STT_MODEL", "large-v3-turbo")
DEVICE = os.getenv("STT_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8_float16")
LANGUAGE = os.getenv("STT_LANGUAGE", "ru")
HOST = os.getenv("STT_HOST", "0.0.0.0")
PORT = int(os.getenv("STT_PORT", "8001"))
SAMPLE_RATE = 16000
PARTIAL_INTERVAL_SEC = float(os.getenv("STT_PARTIAL_INTERVAL_SEC", "1.0"))

state = {"model": None, "loaded": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        log.info("Loading Whisper %s (%s, %s)", MODEL_NAME, DEVICE, COMPUTE_TYPE)
        state["model"] = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
        state["loaded"] = True
        log.info("STT model loaded")
    except Exception:
        log.exception("Failed to load STT model")
        state["loaded"] = False
    yield
    state["model"] = None


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    body = {
        "status": "ok" if state["loaded"] else "degraded",
        "service": "stt",
        "model": MODEL_NAME,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "model_loaded": state["loaded"],
    }
    return JSONResponse(body, status_code=200 if state["loaded"] else 503)


def _transcribe(samples, final: bool) -> str:
    segments, _ = state["model"].transcribe(
        samples,
        language=LANGUAGE,
        beam_size=5 if final else 1,
        vad_filter=True,
    )
    return "".join(seg.text for seg in segments).strip()


@app.websocket("/stt")
async def stt_ws(ws: WebSocket):
    await ws.accept()
    if not state["loaded"]:
        await ws.send_json({"error": "model not loaded"})
        await ws.close()
        return
    buffer = bytearray()
    bytes_since_partial = 0
    partial_threshold = int(PARTIAL_INTERVAL_SEC * SAMPLE_RATE * 2)  # 2 bytes/sample
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            try:
                if msg.get("bytes") is not None:
                    buffer.extend(msg["bytes"])
                    bytes_since_partial += len(msg["bytes"])
                    if bytes_since_partial >= partial_threshold:
                        bytes_since_partial = 0
                        samples = pcm16_to_float32(bytes(buffer))
                        text = await asyncio.to_thread(_transcribe, samples, False)
                        # TODO: this re-transcribes the entire growing buffer on
                        # every partial interval (buffer only clears on end/reset),
                        # so GPU work is ~O(n²) over a long utterance. A sliding-
                        # window / committed-prefix scheme would bound the cost.
                        await ws.send_json({"text": text, "is_final": False, "partial": text})
                elif msg.get("text") is not None:
                    event = json.loads(msg["text"]).get("event")
                    if event == "end":
                        samples = pcm16_to_float32(bytes(buffer))
                        text = await asyncio.to_thread(_transcribe, samples, True)
                        await ws.send_json({"text": text, "is_final": True, "partial": ""})
                        buffer = bytearray()
                        bytes_since_partial = 0
                    elif event == "reset":
                        buffer = bytearray()
                        bytes_since_partial = 0
            except Exception as e:  # noqa: BLE001 — report and keep serving
                log.exception("STT message handling error")
                try:
                    await ws.send_json({"error": str(e)})
                except Exception:
                    pass
                continue
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
