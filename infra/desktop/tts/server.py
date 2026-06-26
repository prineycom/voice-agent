"""TTS WebSocket service — Qwen3-TTS-1.7B (CustomVoice, Russian).

WS /tts:  client sends JSON {"text","voice"}; server streams binary 16-bit PCM
          @ 24kHz mono chunks, then a final JSON {"done": true}.
GET /health: model/load status.
"""

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import synthesize

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tts")

HOST = os.getenv("TTS_HOST", "0.0.0.0")
PORT = int(os.getenv("TTS_PORT", "8002"))

state = {"loaded": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        log.info("Loading TTS engine %s, model %s", synthesize.engine().name, synthesize.engine().model_name)
        synthesize.load_model()
        state["loaded"] = True
        log.info("TTS model loaded")
    except Exception:
        log.exception("Failed to load TTS model")
        state["loaded"] = False
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    eng = synthesize.engine()
    body = {
        "status": "ok" if state["loaded"] else "degraded",
        "service": "tts",
        "engine": eng.name,
        "model": eng.model_name,
        "device": "cuda",
        "language": eng.language,
        "model_loaded": state["loaded"],
    }
    # Engine-specific fields (speaker / ref_profiles / instruct, …).
    body.update(eng.health_fields())
    return JSONResponse(body, status_code=200 if state["loaded"] else 503)


@app.websocket("/tts")
async def tts_ws(ws: WebSocket):
    await ws.accept()
    if not state["loaded"]:
        await ws.send_json({"error": "model not loaded"})
        await ws.close()
        return
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") is None:
                continue
            req = json.loads(msg["text"])
            text = (req.get("text") or "").strip()
            voice = req.get("voice", "default")
            if not text:
                await ws.send_json({"error": "empty text"})
                continue

            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            cancel = threading.Event()

            def produce():
                try:
                    for chunk in synthesize.stream_pcm(text, voice):
                        if cancel.is_set():
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
                except Exception as e:  # noqa: BLE001
                    loop.call_soon_threadsafe(queue.put_nowait, e)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            producer_task = asyncio.create_task(asyncio.to_thread(produce))

            try:
                completed = False
                while True:
                    item = await queue.get()
                    if item is None:
                        completed = True
                        break
                    if isinstance(item, Exception):
                        await ws.send_json({"error": str(item)})
                        break
                    await ws.send_bytes(item)
                if completed:
                    try:
                        await ws.send_json({"done": True})
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                cancel.set()
                await asyncio.gather(producer_task, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.exception("TTS websocket error")
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
