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

import a2f_fork
import synthesize

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tts")

HOST = os.getenv("TTS_HOST", "0.0.0.0")
PORT = int(os.getenv("TTS_PORT", "8002"))

state = {"loaded": False}


def _cuda_empty_cache() -> None:
    """Best-effort VRAM cache flush after unloading a model.

    Import is lazy so the server starts without torch and unit tests on
    CPU-only machines don't fail.
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


@app.post("/unload")
async def unload_model():
    """Unload the TTS model from VRAM. Idempotent."""
    if not state["loaded"]:
        return JSONResponse({"status": "unloaded", "service": "tts", "model_loaded": False}, status_code=200)
    synthesize.unload_model()
    state["loaded"] = False
    _cuda_empty_cache()
    log.info("TTS model unloaded")
    return JSONResponse({"status": "unloaded", "service": "tts", "model_loaded": False}, status_code=200)


@app.post("/reload")
async def reload_model():
    """Reload the TTS model into VRAM. Idempotent."""
    try:
        synthesize.load_model()
        state["loaded"] = True
        log.info("TTS model reloaded")
        return JSONResponse({"status": "loaded", "service": "tts", "model_loaded": True}, status_code=200)
    except Exception as e:
        state["loaded"] = False
        log.exception("Failed to reload TTS model")
        return JSONResponse({"status": "error", "service": "tts", "model_loaded": False, "error": str(e)}, status_code=500)


OUT_QUEUE_MAX = 256


@app.websocket("/tts")
async def tts_ws(ws: WebSocket):
    await ws.accept()
    if not state["loaded"]:
        await ws.send_json({"error": "model not loaded"})
        await ws.close()
        return

    loop = asyncio.get_running_loop()

    # Single-writer outbound queue: every frame to the client — PCM, the audio
    # {"done": true}, forwarded A2F blendshapes, and {"type": "a2f_done"} — is
    # enqueued here and drained by exactly one writer task, so a fork's callbacks
    # (which run from the fork's task) never race a concurrent ws.send_*.
    out_queue: asyncio.Queue = asyncio.Queue(maxsize=OUT_QUEUE_MAX)
    write_failed = asyncio.Event()

    async def writer():
        while True:
            item = await out_queue.get()
            if item is None:
                break
            if write_failed.is_set():
                # Client gone: keep draining so producers never block on put().
                continue
            tag, payload = item
            try:
                if tag == "bin":
                    await ws.send_bytes(payload)
                else:
                    await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — client disconnected mid-stream
                write_failed.set()

    writer_task = asyncio.create_task(writer())

    # Best-effort A2F forks opened on this connection, reaped when it closes.
    forks: list = []
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
            emotion = req.get("emotion")
            if not isinstance(emotion, (str, list)):
                emotion = None
            if not text:
                await out_queue.put(("json", {"error": "empty text"}))
                continue

            producer_queue: asyncio.Queue = asyncio.Queue()
            cancel = threading.Event()

            def produce():
                try:
                    for chunk in synthesize.stream_pcm(text, voice):
                        if cancel.is_set():
                            break
                        loop.call_soon_threadsafe(producer_queue.put_nowait, chunk)
                except Exception as e:  # noqa: BLE001
                    loop.call_soon_threadsafe(producer_queue.put_nowait, e)
                finally:
                    loop.call_soon_threadsafe(producer_queue.put_nowait, None)

            producer_task = asyncio.create_task(asyncio.to_thread(produce))

            # Prune forks whose background drain already finished, so a long-lived
            # connection (the agent pipelines many sentences over one socket) does
            # not retain a completed A2FFork per utterance.
            forks = [f for f in forks if not f.done]

            # Per-sentence gate: the a2f_done marker must land AFTER this
            # sentence's audio {"done": true}. The fork's on_done may fire early
            # (e.g. a fast connect failure), so hold the marker until the handler
            # has enqueued {"done": true}.
            audio_done = asyncio.Event()

            def on_frame(frame):
                # Blendshapes are drop-on-full: A2F backpressure must never stall
                # the audio path, so never block here.
                try:
                    out_queue.put_nowait(("json", frame))
                except asyncio.QueueFull:
                    pass

            def on_done(gate=audio_done):
                # Fires from the fork's task (drain, failure, or cancel). Enqueue
                # exactly one a2f_done, ordered after this sentence's audio done.
                # `gate` is bound per sentence so a later utterance can't rebind it.
                async def _emit():
                    await gate.wait()
                    marker = ("json", {"type": "a2f_done"})
                    try:
                        out_queue.put_nowait(marker)
                    except asyncio.QueueFull:
                        await out_queue.put(marker)

                loop.create_task(_emit())

            # Tee this utterance into the co-located A2F service (best-effort;
            # a fork failure must never affect the client stream or barge-in).
            fork = None
            try:
                fork = a2f_fork.maybe_start_fork(emotion, on_frame=on_frame, on_done=on_done)
            except Exception:  # noqa: BLE001
                log.warning("Failed to start A2F fork", exc_info=True)
                fork = None
            if fork:
                forks.append(fork)

            try:
                completed = False
                while True:
                    item = await producer_queue.get()
                    if item is None:
                        completed = True
                        if fork:
                            fork.end()
                        break
                    if isinstance(item, Exception):
                        await out_queue.put(("json", {"error": str(item)}))
                        break
                    await out_queue.put(("bin", item))
                    if fork:
                        fork.feed(item)
                    if write_failed.is_set():
                        break
                if completed:
                    await out_queue.put(("json", {"done": True}))
                    # No fork means on_done never fires; emit the marker ourselves
                    # so the client always sees exactly one a2f_done per sentence.
                    if fork is None:
                        await out_queue.put(("json", {"type": "a2f_done"}))
            finally:
                cancel.set()
                # Release any gated a2f_done emitter (the fork's on_done may fire
                # after audio done, or never having connected).
                audio_done.set()
                await asyncio.gather(producer_task, return_exceptions=True)
                if fork and not completed:
                    await fork.close()
            if write_failed.is_set():
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.exception("TTS websocket error")
        try:
            await out_queue.put(("json", {"error": str(e)}))
        except Exception:
            pass
    finally:
        for f in forks:
            await f.close()
        # Stop the single writer after forks are closed, then drain it.
        await out_queue.put(None)
        await writer_task


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
