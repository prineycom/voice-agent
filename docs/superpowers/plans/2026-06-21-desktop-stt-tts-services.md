# Desktop STT & TTS WebSocket Services — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up two GPU-backed FastAPI WebSocket services on the Desktop (Windows 11, RTX 4070) — `/stt` (faster-whisper large-v3-turbo) and `/tts` (Qwen3-TTS-1.7B) — reachable from the Pi over Tailscale.

**Architecture:** Two independent services, each in its own Python venv and uvicorn process. STT (port 8001) uses CTranslate2 (no torch); TTS (port 8002) uses torch + faster-qwen3-tts. Code is authored in `infra/desktop/` in this repo and deployed to `C:\Users\Pavel\voice-agent\desktop\` via `scp`. Pure audio helpers are unit-tested with pytest; full services are smoke-tested over WebSocket on the Desktop.

**Tech Stack:** Python 3.10/3.12, FastAPI, uvicorn[standard], faster-whisper (CTranslate2, CUDA 12 wheels), faster-qwen3-tts (torch cu12x), numpy, soxr, soundfile, websockets, pytest.

**Spec:** `docs/superpowers/specs/2026-06-21-desktop-stt-tts-services-design.md`

**Conventions:**
- Desktop SSH: `ssh Pavel@100.75.88.35` (cmd.exe shell, passwordless from Pi). Commands use cmd.exe `&` separators.
- Commit messages: conventional commits, reference `#2` (STT) and `#3` (TTS). End with the Co-Authored-By trailer.
- Work on branch `epic-2-3-desktop-stt-tts` (already created).

---

## File Structure

```
infra/desktop/
├── README.md                 # setup + startup runbook (Task R1)
├── .gitignore                # ignore venvs, models, __pycache__, *.wav (Task 0)
├── stt/
│   ├── audio.py              # pure helpers: pcm16_to_float32 (Task 1)
│   ├── server.py             # FastAPI WS /stt + /health (Task 3)
│   ├── requirements.txt      # (Task 2)
│   ├── start-stt.bat         # (Task 2)
│   ├── .env.example          # (Task 2)
│   └── tests/test_audio.py   # pytest for audio.py (Task 1)
├── tts/
│   ├── audio.py              # pure helpers: float32_to_pcm16, resample_to_24k (Task 5)
│   ├── synthesize.py         # Qwen3-TTS adapter: load_model, stream_pcm (Task 7)
│   ├── server.py             # FastAPI WS /tts + /health (Task 8)
│   ├── requirements.txt      # (Task 6)
│   ├── start-tts.bat         # (Task 6)
│   ├── .env.example          # (Task 6)
│   └── tests/test_audio.py   # pytest for audio.py (Task 5)
└── scripts/
    ├── smoke_stt.py          # WS client: stream WAV → /stt (Task 4)
    └── smoke_tts.py          # WS client: text → /tts → WAV (Task 9)
```

Files NOT committed (live on Desktop only): `stt/.venv/`, `tts/.venv/`, `stt/.env`, `tts/.env`, downloaded models, generated `*.wav`.

---

## Phase 0 — Setup

### Task 0: Branch, directories, gitignore

**Files:**
- Create: `infra/desktop/.gitignore`

- [ ] **Step 1: Confirm branch**

Run: `git branch --show-current`
Expected: `epic-2-3-desktop-stt-tts`

- [ ] **Step 2: Create directory tree**

```bash
mkdir -p infra/desktop/stt/tests infra/desktop/tts/tests infra/desktop/scripts
```

- [ ] **Step 3: Write `infra/desktop/.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.env
*.wav
models/
```

- [ ] **Step 4: Commit**

```bash
git add infra/desktop/.gitignore
git commit -m "#2 #3 chore(desktop): scaffold infra/desktop dirs and gitignore

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 1 — STT service (Epic #2)

### Task 1: STT audio helper (TDD)

**Files:**
- Create: `infra/desktop/stt/audio.py`
- Test: `infra/desktop/stt/tests/test_audio.py`

- [ ] **Step 1: Write the failing test**

`infra/desktop/stt/tests/test_audio.py`:
```python
import numpy as np
from audio import pcm16_to_float32


def test_pcm16_to_float32_scales_to_unit_range():
    data = np.array([-32768, 0, 32767], dtype="<i2").tobytes()
    out = pcm16_to_float32(data)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, [-1.0, 0.0, 32767 / 32768], rtol=1e-6)


def test_pcm16_to_float32_drops_odd_trailing_byte():
    data = b"\x00\x00\x01"  # 3 bytes -> 1 valid sample
    out = pcm16_to_float32(data)
    assert len(out) == 1


def test_pcm16_to_float32_empty():
    out = pcm16_to_float32(b"")
    assert len(out) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run (on Desktop after venv exists, or locally with numpy):
`cd infra/desktop/stt && python -m pytest tests/test_audio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audio'`

- [ ] **Step 3: Write minimal implementation**

`infra/desktop/stt/audio.py`:
```python
"""Pure audio helpers for the STT service (no GPU, no model deps)."""

import numpy as np


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Convert little-endian 16-bit signed PCM bytes to float32 in [-1, 1).

    A trailing odd byte (incomplete sample) is dropped.
    """
    if len(data) % 2 != 0:
        data = data[: len(data) - 1]
    ints = np.frombuffer(data, dtype="<i2").astype(np.float32)
    return ints / 32768.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd infra/desktop/stt && python -m pytest tests/test_audio.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add infra/desktop/stt/audio.py infra/desktop/stt/tests/test_audio.py
git commit -m "#2 feat(stt): add pcm16_to_float32 audio helper with tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 2: STT requirements, env, startup script

**Files:**
- Create: `infra/desktop/stt/requirements.txt`
- Create: `infra/desktop/stt/.env.example`
- Create: `infra/desktop/stt/start-stt.bat`

- [ ] **Step 1: Write `requirements.txt`**

```text
# faster-whisper pulls ctranslate2 + nvidia-cublas-cu12 + nvidia-cudnn-cu12.
# CTranslate2 4.5+ uses CUDA 12 / cuDNN 9 wheels — runs on the CUDA 13.2 driver
# via forward compatibility. No system CUDA toolkit needed.
faster-whisper
fastapi
uvicorn[standard]
numpy
python-dotenv
# dev / smoke-test
pytest
websockets
```

- [ ] **Step 2: Write `.env.example`**

```text
STT_HOST=0.0.0.0
STT_PORT=8001
STT_MODEL=large-v3-turbo
STT_DEVICE=cuda
STT_COMPUTE_TYPE=int8_float16
STT_LANGUAGE=ru
STT_PARTIAL_INTERVAL_SEC=1.0
```

- [ ] **Step 3: Write `start-stt.bat`**

```bat
@echo off
cd /d %~dp0
call .venv\Scripts\activate.bat
uvicorn server:app --host 0.0.0.0 --port 8001
```

- [ ] **Step 4: Commit**

```bash
git add infra/desktop/stt/requirements.txt infra/desktop/stt/.env.example infra/desktop/stt/start-stt.bat
git commit -m "#2 feat(stt): add requirements, env example, startup script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 3: STT FastAPI server

**Files:**
- Create: `infra/desktop/stt/server.py`

- [ ] **Step 1: Write `server.py`**

```python
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
            if msg.get("bytes") is not None:
                buffer.extend(msg["bytes"])
                bytes_since_partial += len(msg["bytes"])
                if bytes_since_partial >= partial_threshold:
                    bytes_since_partial = 0
                    samples = pcm16_to_float32(bytes(buffer))
                    text = await asyncio.to_thread(_transcribe, samples, False)
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
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001 — report and keep serving
        log.exception("STT websocket error")
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
```

- [ ] **Step 2: Commit**

```bash
git add infra/desktop/stt/server.py
git commit -m "#2 feat(stt): add FastAPI WS /stt + /health server

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4: STT smoke-test client

**Files:**
- Create: `infra/desktop/scripts/smoke_stt.py`

- [ ] **Step 1: Write `smoke_stt.py`**

```python
"""Smoke test: stream a 16kHz mono WAV to /stt and print transcripts.

Usage: python smoke_stt.py path/to/16k_mono.wav [ws://host:8001/stt]
"""

import asyncio
import json
import sys
import wave

import websockets


async def main(wav_path: str, url: str = "ws://localhost:8001/stt"):
    wf = wave.open(wav_path, "rb")
    assert wf.getframerate() == 16000, f"need 16kHz, got {wf.getframerate()}"
    assert wf.getnchannels() == 1, "need mono"
    assert wf.getsampwidth() == 2, "need 16-bit"
    pcm = wf.readframes(wf.getnframes())
    wf.close()

    async with websockets.connect(url, max_size=None) as ws:
        chunk = 1024  # ~32ms
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i : i + chunk])
        await ws.send(json.dumps({"event": "end"}))
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            data = json.loads(msg)
            print(data)
            if data.get("is_final") or data.get("error"):
                break


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
```

- [ ] **Step 2: Commit**

```bash
git add infra/desktop/scripts/smoke_stt.py
git commit -m "#2 test(stt): add /stt websocket smoke-test client

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task D1: Deploy STT to Desktop, create venv, install, run unit tests

> Deployment + remote install. Run from the Pi.

- [ ] **Step 1: Create deploy dir on Desktop**

Run: `ssh Pavel@100.75.88.35 "mkdir C:\Users\Pavel\voice-agent\desktop 2>nul & echo ok"`
Expected: `ok`

- [ ] **Step 2: Copy STT + scripts to Desktop**

```bash
scp -r infra/desktop/stt Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/
scp -r infra/desktop/scripts Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/
```

- [ ] **Step 3: Create venv (Python 3.12) and upgrade pip**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & py -3.12 -m venv .venv & .venv\Scripts\python -m pip install -U pip"
```
Expected: pip upgrade success.

- [ ] **Step 4: Install requirements**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & .venv\Scripts\pip install -r requirements.txt"
```
Expected: faster-whisper, ctranslate2, nvidia-cublas-cu12, nvidia-cudnn-cu12, fastapi, uvicorn installed. Capture `pip freeze` for the record if desired.

- [ ] **Step 5: Run unit tests on Desktop**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & .venv\Scripts\python -m pytest tests/test_audio.py -v"
```
Expected: 3 passed.

- [ ] **Step 6: Create `.env` from example**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & copy .env.example .env"
```
Expected: `1 file(s) copied.`

### Task D2: Download model & verify STT loads on CUDA

- [ ] **Step 1: Warm the model (downloads large-v3-turbo, ~1.5GB) and confirm CUDA load**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & .venv\Scripts\python -c \"from faster_whisper import WhisperModel; m=WhisperModel('large-v3-turbo', device='cuda', compute_type='int8_float16'); print('LOADED', m.model.device)\""
```
Expected: prints `LOADED cuda` (model downloaded to HF cache on first run).
**If it fails** with a cuDNN/cublas DLL error: confirm `nvidia-cudnn-cu12` and `nvidia-cublas-cu12` are installed in the venv (`pip show nvidia-cudnn-cu12`); their DLLs ship in `.venv\Lib\site-packages\nvidia\*\bin`. As a fallback, set `compute_type=float16`.

### Task D3: Add Windows Firewall rule + run STT + smoke test

- [ ] **Step 1: Add inbound firewall rule for 8001 (run once; may need elevation)**

Run:
```
ssh Pavel@100.75.88.35 "netsh advfirewall firewall add rule name=voiceagent-stt dir=in action=allow protocol=TCP localport=8001"
```
Expected: `Ok.` (If "requires elevation", document that the user runs it from an elevated shell — see README.)

- [ ] **Step 2: Start the STT service in the background**

Run (background task so it stays up):
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & .venv\Scripts\python -m uvicorn server:app --host 0.0.0.0 --port 8001"
```
Run this as a background command. Expected log: `STT model loaded` then `Uvicorn running on http://0.0.0.0:8001`.

- [ ] **Step 3: Check health from the Pi**

Run: `curl -s http://100.75.88.35:8001/health`
Expected JSON: `{"status":"ok","service":"stt",...,"model_loaded":true}`

- [ ] **Step 4: Smoke test deferred to Task D6**

The STT smoke test needs a 16kHz Russian WAV. We generate it from the TTS service output (Task D6), giving an end-to-end loop. Mark this step done once TTS exists; for now, confirm `/health` is `ok`.

---

## Phase 2 — TTS service (Epic #3)

### Task 5: TTS audio helpers (TDD)

**Files:**
- Create: `infra/desktop/tts/audio.py`
- Test: `infra/desktop/tts/tests/test_audio.py`

- [ ] **Step 1: Write the failing test**

`infra/desktop/tts/tests/test_audio.py`:
```python
import numpy as np
from audio import float32_to_pcm16, resample_to_24k


def test_float32_to_pcm16_scales_and_clips():
    out = float32_to_pcm16(np.array([0.0, 1.0, -1.0, 2.0, -2.0], dtype=np.float32))
    ints = np.frombuffer(out, dtype="<i2")
    assert list(ints) == [0, 32767, -32767, 32767, -32767]


def test_resample_to_24k_noop_when_already_24k():
    x = np.zeros(100, dtype=np.float32)
    out = resample_to_24k(x, 24000)
    assert len(out) == 100
    assert out.dtype == np.float32


def test_resample_to_24k_halves_length_from_48k():
    x = np.zeros(48000, dtype=np.float32)  # 1s @ 48k
    out = resample_to_24k(x, 48000)
    assert abs(len(out) - 24000) < 100  # ~1s @ 24k
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd infra/desktop/tts && python -m pytest tests/test_audio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audio'`

- [ ] **Step 3: Write minimal implementation**

`infra/desktop/tts/audio.py`:
```python
"""Pure audio helpers for the TTS service (no GPU, no model deps)."""

import numpy as np
import soxr


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Convert float32 audio in [-1, 1] to little-endian 16-bit PCM bytes."""
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    ints = (clipped * 32767.0).round().astype("<i2")
    return ints.tobytes()


def resample_to_24k(samples: np.ndarray, src_sr: int) -> np.ndarray:
    """Resample float32 mono audio to 24kHz. No-op when already 24kHz."""
    samples = np.asarray(samples, dtype=np.float32)
    if src_sr == 24000:
        return samples
    return soxr.resample(samples, src_sr, 24000).astype(np.float32)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd infra/desktop/tts && python -m pytest tests/test_audio.py -v`
Expected: PASS (3 passed). (Requires `numpy` + `soxr`; run on Desktop after Task D4 if not available locally.)

- [ ] **Step 5: Commit**

```bash
git add infra/desktop/tts/audio.py infra/desktop/tts/tests/test_audio.py
git commit -m "#3 feat(tts): add float32_to_pcm16 + resample_to_24k helpers with tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 6: TTS requirements, env, startup script

**Files:**
- Create: `infra/desktop/tts/requirements.txt`
- Create: `infra/desktop/tts/.env.example`
- Create: `infra/desktop/tts/start-tts.bat`

- [ ] **Step 1: Write `requirements.txt`**

```text
# Install torch FIRST with the CUDA 12 wheel index (see README), then this file:
#   pip install torch --index-url https://download.pytorch.org/whl/cu124
# faster-qwen3-tts requires torch >= 2.5.1 with CUDA.
faster-qwen3-tts
fastapi
uvicorn[standard]
soundfile
soxr
numpy
python-dotenv
# dev / smoke-test
pytest
websockets
```

- [ ] **Step 2: Write `.env.example`**

```text
TTS_HOST=0.0.0.0
TTS_PORT=8002
TTS_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
TTS_SPEAKER=aiden
TTS_LANGUAGE=Russian
TTS_CHUNK_SIZE=4
```

- [ ] **Step 3: Write `start-tts.bat`**

```bat
@echo off
cd /d %~dp0
call .venv\Scripts\activate.bat
uvicorn server:app --host 0.0.0.0 --port 8002
```

- [ ] **Step 4: Commit**

```bash
git add infra/desktop/tts/requirements.txt infra/desktop/tts/.env.example infra/desktop/tts/start-tts.bat
git commit -m "#3 feat(tts): add requirements, env example, startup script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 7: TTS synthesize adapter

**Files:**
- Create: `infra/desktop/tts/synthesize.py`

> This module isolates the Qwen3-TTS call behind `load_model()` / `stream_pcm()`.
> The exact streaming method name is confirmed empirically in Task D5; the default
> below targets CustomVoice streaming, with documented fallbacks.

- [ ] **Step 1: Write `synthesize.py`**

```python
"""Qwen3-TTS synthesis adapter.

Isolates the model call so the WebSocket server stays backend-agnostic.
`stream_pcm` yields 24kHz mono int16 PCM byte chunks.

Backend method name is confirmed via introspection (plan Task D5). Default path
uses CustomVoice streaming; see FALLBACKS at the bottom if that method is absent.
"""

import logging
import os

import numpy as np

from audio import float32_to_pcm16, resample_to_24k

log = logging.getLogger("tts.synthesize")

MODEL_NAME = os.getenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
SPEAKER = os.getenv("TTS_SPEAKER", "aiden")
LANGUAGE = os.getenv("TTS_LANGUAGE", "Russian")
CHUNK_SIZE = int(os.getenv("TTS_CHUNK_SIZE", "4"))

_model = None


def load_model():
    """Load the Qwen3-TTS model into VRAM. Call once at startup."""
    global _model
    from faster_qwen3_tts import FasterQwen3TTS

    _model = FasterQwen3TTS.from_pretrained(MODEL_NAME)
    return _model


def is_loaded() -> bool:
    return _model is not None


def stream_pcm(text: str, voice: str = "default"):
    """Yield 24kHz mono int16 PCM byte chunks for `text`.

    `voice` overrides the configured speaker unless it is empty/"default".
    """
    speaker = SPEAKER if voice in (None, "", "default") else voice
    for audio_chunk, sr, *_ in _model.generate_custom_voice_streaming(
        text=text,
        language=LANGUAGE,
        speaker=speaker,
        chunk_size=CHUNK_SIZE,
    ):
        samples = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        samples = resample_to_24k(samples, int(sr))
        yield float32_to_pcm16(samples)


# FALLBACKS (apply in Task D5 if generate_custom_voice_streaming is not exposed):
#
# (a) Non-streaming custom voice — wrap the single returned array as one chunk:
#       audio, sr = _model.generate_custom_voice(text=text, language=LANGUAGE,
#                                                 speaker=speaker)
#       yield float32_to_pcm16(resample_to_24k(np.asarray(audio, np.float32), int(sr)))
#
# (b) Voice clone streaming (requires a Russian reference clip on disk):
#       for audio_chunk, sr, *_ in _model.generate_voice_clone_streaming(
#           text=text, language=LANGUAGE, ref_audio=REF_WAV, ref_text=REF_TEXT,
#           chunk_size=CHUNK_SIZE):
#           ...
#
# (c) Official package `qwen-tts`:
#       from qwen_tts import Qwen3TTSModel
#       model.generate_custom_voice(...) with its documented signature.
```

- [ ] **Step 2: Commit**

```bash
git add infra/desktop/tts/synthesize.py
git commit -m "#3 feat(tts): add Qwen3-TTS synthesize adapter (CustomVoice streaming)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 8: TTS FastAPI server

**Files:**
- Create: `infra/desktop/tts/server.py`

- [ ] **Step 1: Write `server.py`**

```python
"""TTS WebSocket service — Qwen3-TTS-1.7B (CustomVoice, Russian).

WS /tts:  client sends JSON {"text","voice"}; server streams binary 16-bit PCM
          @ 24kHz mono chunks, then a final JSON {"done": true}.
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
        log.info("Loading TTS model %s", synthesize.MODEL_NAME)
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
    body = {
        "status": "ok" if state["loaded"] else "degraded",
        "service": "tts",
        "model": synthesize.MODEL_NAME,
        "device": "cuda",
        "speaker": synthesize.SPEAKER,
        "language": synthesize.LANGUAGE,
        "model_loaded": state["loaded"],
    }
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

            def produce():
                try:
                    for chunk in synthesize.stream_pcm(text, voice):
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
                except Exception as e:  # noqa: BLE001
                    loop.call_soon_threadsafe(queue.put_nowait, e)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            asyncio.create_task(asyncio.to_thread(produce))

            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    await ws.send_json({"error": str(item)})
                    break
                await ws.send_bytes(item)
            await ws.send_json({"done": True})
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
```

- [ ] **Step 2: Commit**

```bash
git add infra/desktop/tts/server.py
git commit -m "#3 feat(tts): add FastAPI WS /tts + /health server

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 9: TTS smoke-test client

**Files:**
- Create: `infra/desktop/scripts/smoke_tts.py`

- [ ] **Step 1: Write `smoke_tts.py`**

```python
"""Smoke test: send Russian text to /tts, save 24kHz WAV, report first-chunk latency.

Usage: python smoke_tts.py "текст" [out.wav] [ws://host:8002/tts]
"""

import asyncio
import json
import sys
import time
import wave

import websockets


async def main(text: str, out_path: str = "tts_out.wav", url: str = "ws://localhost:8002/tts"):
    async with websockets.connect(url, max_size=None) as ws:
        t0 = time.time()
        await ws.send(json.dumps({"text": text, "voice": "default"}))
        pcm = bytearray()
        first_ms = None
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                if first_ms is None:
                    first_ms = (time.time() - t0) * 1000
                    print(f"first chunk: {first_ms:.0f} ms")
                pcm.extend(msg)
            else:
                data = json.loads(msg)
                if data.get("error"):
                    print("ERROR", data)
                    return
                if data.get("done"):
                    break
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(bytes(pcm))
        print(f"wrote {out_path}: {len(pcm)} bytes (~{len(pcm)/2/24000:.1f}s)")


if __name__ == "__main__":
    args = sys.argv[1:]
    text = args[0] if args else "Привет! Это тест синтеза речи."
    asyncio.run(main(text, *args[1:]))
```

- [ ] **Step 2: Commit**

```bash
git add infra/desktop/scripts/smoke_tts.py
git commit -m "#3 test(tts): add /tts websocket smoke-test client

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task D4: Deploy TTS to Desktop, create venv, install torch + deps, run unit tests

- [ ] **Step 1: Copy TTS + refreshed scripts to Desktop**

```bash
scp -r infra/desktop/tts Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/
scp -r infra/desktop/scripts Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/
```

- [ ] **Step 2: Create venv (Python 3.12) and upgrade pip**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & py -3.12 -m venv .venv & .venv\Scripts\python -m pip install -U pip"
```

- [ ] **Step 3: Install torch with CUDA 12 wheels**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu124"
```
Expected: torch (cu124) installed. (If cu124 unavailable, try cu126 / cu121 — document whichever works.)

- [ ] **Step 4: Verify torch sees CUDA**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -c \"import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))\""
```
Expected: `2.x.x+cu124 True NVIDIA GeForce RTX 4070`

- [ ] **Step 5: Install the rest of requirements**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\pip install -r requirements.txt"
```
Expected: faster-qwen3-tts, transformers, soundfile, soxr, fastapi, uvicorn installed.

- [ ] **Step 6: Run unit tests on Desktop**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -m pytest tests/test_audio.py -v"
```
Expected: 3 passed.

- [ ] **Step 7: Create `.env` from example**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & copy .env.example .env"
```

### Task D5: Resolve TTS API, download model, verify load

- [ ] **Step 1: Introspect the faster-qwen3-tts API**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -c \"import faster_qwen3_tts as f; from faster_qwen3_tts import FasterQwen3TTS as M; print([x for x in dir(M) if 'generate' in x])\""
```
Expected: a list of `generate_*` methods. **Confirm `generate_custom_voice_streaming` exists.** If not, pick the closest streaming method and update `synthesize.py:stream_pcm` per the FALLBACKS comment (then re-deploy with `scp` and re-commit).

- [ ] **Step 2: Inspect the chosen method's signature**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -c \"import inspect; from faster_qwen3_tts import FasterQwen3TTS as M; print(inspect.signature(M.generate_custom_voice_streaming))\""
```
Expected: parameter names (confirm `text`, `language`, `speaker`/`spk`, `chunk_size`). Adjust `synthesize.py` argument names to match exactly if they differ; re-deploy + re-commit.

- [ ] **Step 3: Download the model and confirm it loads on CUDA**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -c \"import synthesize; synthesize.load_model(); print('TTS LOADED', synthesize.is_loaded())\""
```
Expected: model downloads (~4GB) on first run, prints `TTS LOADED True`.

- [ ] **Step 4: List valid CustomVoice speakers and confirm the configured one**

Run (method name may vary — try the documented one, else check the model card):
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -c \"from faster_qwen3_tts import FasterQwen3TTS as M; print(getattr(M,'list_speakers',lambda:None)())\""
```
Expected: a list of speaker names. If `aiden` is not present, set `TTS_SPEAKER` in `.env` to a valid one (prefer a Russian-capable / neutral speaker) and document the choice.

### Task D6: Run TTS, smoke test, then STT end-to-end loop

- [ ] **Step 1: Firewall rule for 8002**

Run:
```
ssh Pavel@100.75.88.35 "netsh advfirewall firewall add rule name=voiceagent-tts dir=in action=allow protocol=TCP localport=8002"
```
Expected: `Ok.` (or document elevation requirement).

- [ ] **Step 2: Start the TTS service in the background**

Run as a background command:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\python -m uvicorn server:app --host 0.0.0.0 --port 8002"
```
Expected log: `TTS model loaded` then `Uvicorn running on http://0.0.0.0:8002`.

- [ ] **Step 3: Health check from the Pi**

Run: `curl -s http://100.75.88.35:8002/health`
Expected: `{"status":"ok","service":"tts",...,"model_loaded":true}`

- [ ] **Step 4: TTS smoke test on the Desktop (writes WAV, prints first-chunk latency)**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\scripts & ..\tts\.venv\Scripts\python smoke_tts.py \"Привет! Это тест синтеза речи.\" tts_out.wav"
```
Expected: prints `first chunk: <N> ms` and `wrote tts_out.wav`. Pull the WAV to the Pi to listen: `scp Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/scripts/tts_out.wav /tmp/`.

- [ ] **Step 5: Build a 16kHz STT input from the TTS output (end-to-end loop)**

Run (ffmpeg is on the Desktop):
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\scripts & ffmpeg -y -i tts_out.wav -ar 16000 -ac 1 -sample_fmt s16 stt_in.wav"
```
Expected: `stt_in.wav` written (16kHz mono).

- [ ] **Step 6: STT smoke test (uses the STT venv's python + websockets)**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\scripts & ..\stt\.venv\Scripts\python smoke_stt.py stt_in.wav"
```
Expected: prints partial(s) then `{"text": "...", "is_final": true, ...}` with recognizable Russian text matching the synthesized sentence.

---

## Phase 3 — Documentation & wrap-up

### Task R1: Write the Desktop runbook

**Files:**
- Create: `infra/desktop/README.md`

- [ ] **Step 1: Write `README.md`**

````markdown
# Desktop GPU Services — STT & TTS

GPU inference services for the Voice Agent, running on the Desktop (Windows 11,
RTX 4070 12GB). Reached from the Pi over Tailscale at `100.75.88.35`.

| Service | Port | Endpoint(s) | Model |
| --- | --- | --- | --- |
| STT | 8001 | `WS /stt`, `GET /health` | faster-whisper `large-v3-turbo` (int8_float16) |
| TTS | 8002 | `WS /tts`, `GET /health` | Qwen3-TTS-12Hz-1.7B-CustomVoice (Russian) |

Each service has its own venv. Deploy target: `C:\Users\Pavel\voice-agent\desktop\`.

## Prerequisites (verified 2026-06-21)
- Windows 11, NVIDIA driver 596.49 (CUDA 13.2), RTX 4070 12GB.
- Python 3.12 via the `py` launcher; ffmpeg on PATH.

## Setup

### STT
```bat
cd C:\Users\Pavel\voice-agent\desktop\stt
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

### TTS
```bat
cd C:\Users\Pavel\voice-agent\desktop\tts
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu124
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

## Firewall (run once, elevated PowerShell/cmd)
```bat
netsh advfirewall firewall add rule name=voiceagent-stt dir=in action=allow protocol=TCP localport=8001
netsh advfirewall firewall add rule name=voiceagent-tts dir=in action=allow protocol=TCP localport=8002
```

## Start the services
```bat
:: STT  (downloads large-v3-turbo on first run)
C:\Users\Pavel\voice-agent\desktop\stt\start-stt.bat

:: TTS  (downloads Qwen3-TTS-1.7B on first run)
C:\Users\Pavel\voice-agent\desktop\tts\start-tts.bat
```
Each loads its model into VRAM at startup and keeps it warm. Combined VRAM ≈ 6.5GB.

## Health
```bash
curl http://100.75.88.35:8001/health
curl http://100.75.88.35:8002/health
```

## API contracts

### `WS /stt`
- Client → Server: binary 16-bit PCM, 16kHz, mono frames; JSON `{"event":"end"}` to
  flush a final transcript, `{"event":"reset"}` to clear the buffer.
- Server → Client: JSON `{"text": "...", "is_final": true|false, "partial": "..."}`.

### `WS /tts`
- Client → Server: JSON `{"text": "...", "voice": "default"}`.
- Server → Client: binary 16-bit PCM, 24kHz, mono chunks, then JSON `{"done": true}`.

## Smoke tests
```bat
:: TTS: text -> WAV
cd C:\Users\Pavel\voice-agent\desktop\scripts
..\tts\.venv\Scripts\python smoke_tts.py "Привет! Это тест." tts_out.wav

:: STT: WAV -> transcript (16kHz mono input)
ffmpeg -y -i tts_out.wav -ar 16000 -ac 1 -sample_fmt s16 stt_in.wav
..\stt\.venv\Scripts\python smoke_stt.py stt_in.wav
```

## Configuration
Per-service `.env` (see `.env.example`). Notable keys: `STT_COMPUTE_TYPE`
(`int8_float16`, fall back to `float16`), `TTS_SPEAKER`, `TTS_CHUNK_SIZE`
(lower = lower first-chunk latency).

## Notes
- Persistence is manual start for now; auto-start as a Windows service is a later task.
- No auth on the WebSocket endpoints — access is constrained to Tailscale + Windows Firewall.
````

- [ ] **Step 2: Commit**

```bash
git add infra/desktop/README.md
git commit -m "#2 #3 docs(desktop): add STT/TTS setup + startup runbook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task R2: Final verification & lockfiles

- [ ] **Step 1: Capture installed versions for reproducibility**

Run:
```
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\stt & .venv\Scripts\pip freeze > requirements.lock.txt"
ssh Pavel@100.75.88.35 "cd C:\Users\Pavel\voice-agent\desktop\tts & .venv\Scripts\pip freeze > requirements.lock.txt"
```
Then pull both back and commit:
```bash
scp Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/stt/requirements.lock.txt infra/desktop/stt/
scp Pavel@100.75.88.35:C:/Users/Pavel/voice-agent/desktop/tts/requirements.lock.txt infra/desktop/tts/
git add infra/desktop/stt/requirements.lock.txt infra/desktop/tts/requirements.lock.txt
git commit -m "#2 #3 chore(desktop): pin installed dependency versions (lockfiles)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 2: Verify both `/health` endpoints return ok and re-confirm both smoke tests pass (Task D6).** Record results.

- [ ] **Step 3: Acceptance check against the spec** — tick every box in the spec's "Acceptance criteria" section. Both services load on CUDA, both endpoints behave per contract, runbook documents startup.

---

## Self-Review

**Spec coverage:**
- STT install + model + `/stt` + Russian + partials/final + `/health` → Tasks 1–4, D1–D3, D6. ✓
- TTS install + model + `/tts` + Russian CustomVoice + streaming + `/health` → Tasks 5–9, D4–D6. ✓
- Startup docs → Task R1. ✓
- Separate venvs/ports, VRAM budget, firewall, error handling → covered in server code + D-tasks. ✓
- Unit tests for pure helpers (TDD) → Tasks 1, 5. ✓
- Integration smoke tests → Tasks D6. ✓

**Placeholder scan:** No "TBD"/"TODO" left as work items. The TTS API uncertainty is handled by an explicit introspection task (D5) with concrete fallbacks, not a placeholder.

**Type/name consistency:** `pcm16_to_float32` (stt/audio.py) used in stt/server.py ✓. `float32_to_pcm16` + `resample_to_24k` (tts/audio.py) used in tts/synthesize.py ✓. `synthesize.load_model/is_loaded/stream_pcm/MODEL_NAME/SPEAKER/LANGUAGE` defined in Task 7 and used in tts/server.py Task 8 ✓. `.env` keys match between `.env.example` and `os.getenv` reads ✓.

**Known live-resolution points (by design, not gaps):** torch CUDA wheel tag (cu124/cu126/cu121), exact TTS streaming method name + speaker list, and `int8_float16` vs `float16` fallback — each has an explicit verification step with a documented fallback.
