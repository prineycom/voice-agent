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
import sys
from contextlib import asynccontextmanager


def _register_cuda_dll_dirs() -> None:
    """On Windows, add the nvidia-*-cu12 wheels' bin dirs to the DLL search path.

    CTranslate2 (faster-whisper backend) links cuBLAS/cuDNN at runtime. The pip
    wheels ship the DLLs under site-packages/nvidia/<lib>/bin, but Windows does
    not search there automatically, so ctranslate2 fails with
    "cublas64_12.dll is not found". No-op on Linux (RPATH handles it).
    """
    if sys.platform != "win32":
        return
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    if not spec or not spec.submodule_search_locations:
        return
    nvidia_root = spec.submodule_search_locations[0]
    for sub in os.listdir(nvidia_root):
        bin_dir = os.path.join(nvidia_root, sub, "bin")
        if os.path.isdir(bin_dir):
            os.add_dll_directory(bin_dir)
            # CTranslate2 resolves cuBLAS/cuDNN via the default loader, which
            # searches PATH (not add_dll_directory dirs), so prepend it too.
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dll_dirs()

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
# Recognition language. "auto" (or empty) → faster-whisper detects the language
# per utterance, which a bilingual (RU/EN) agent needs. A concrete code like
# "ru"/"en" forces that language (lower latency, no misdetect on short clips).
_lang = os.getenv("STT_LANGUAGE", "ru").strip()
LANGUAGE = None if _lang.lower() in ("", "auto") else _lang
HOST = os.getenv("STT_HOST", "0.0.0.0")
PORT = int(os.getenv("STT_PORT", "8001"))
SAMPLE_RATE = 16000
PARTIAL_INTERVAL_SEC = float(os.getenv("STT_PARTIAL_INTERVAL_SEC", "1.0"))

state = {"model": None, "loaded": False}


def _cuda_empty_cache() -> None:
    """Best-effort VRAM cache flush after unloading a model.

    faster-whisper backs onto CTranslate2 (not torch), so torch may not be
    installed in this venv; releasing the `WhisperModel` object is what actually
    frees its VRAM. This call only helps when torch allocated CUDA memory in the
    same process, and is a no-op otherwise. Import is lazy so the server starts
    without torch and so unit tests on CPU-only machines don't fail.
    """
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — cleanup hint, never fatal
        log.debug("torch.cuda.empty_cache unavailable, skipped")


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


@app.post("/unload")
async def unload_model():
    """Unload the STT model from VRAM. Idempotent."""
    if not state["loaded"]:
        return JSONResponse({"status": "unloaded", "service": "stt", "model_loaded": False}, status_code=200)
    state["model"] = None
    state["loaded"] = False
    _cuda_empty_cache()
    log.info("STT model unloaded")
    return JSONResponse({"status": "unloaded", "service": "stt", "model_loaded": False}, status_code=200)


@app.post("/reload")
async def reload_model():
    """Reload the STT model into VRAM. Idempotent."""
    try:
        log.info("Reloading Whisper %s (%s, %s)", MODEL_NAME, DEVICE, COMPUTE_TYPE)
        state["model"] = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
        state["loaded"] = True
        log.info("STT model reloaded")
        return JSONResponse({"status": "loaded", "service": "stt", "model_loaded": True}, status_code=200)
    except Exception as e:
        state["loaded"] = False
        log.exception("Failed to reload STT model")
        return JSONResponse({"status": "error", "service": "stt", "model_loaded": False, "error": str(e)}, status_code=500)


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
