---
tags:
  - voice-agent
  - spec
  - epic-2
  - epic-3
status: approved
date: 2026-06-21
---

# Desktop GPU Services — STT & TTS WebSocket Services (Epic 2 + Epic 3)

## Summary

Two independent FastAPI WebSocket services run on the Desktop (Windows 11, RTX 4070
12 GB) and provide GPU inference for the Agent Worker on the Pi 5:

- **STT** (`/stt`): binary PCM audio in → JSON transcript out, via faster-whisper
  `large-v3-turbo` (CTranslate2).
- **TTS** (`/tts`): JSON text in → binary PCM audio out, via Qwen3-TTS-1.7B
  (faster-qwen3-tts).

Both are reached over Tailscale at `100.75.88.35`. Transport is WebSocket binary
frames per [ADR-0003](../../adr/0003-websocket-stt-tts.md).

## Verified Desktop environment (probed via SSH, 2026-06-21)

| Item | Value |
| --- | --- |
| OS | Windows 11 Pro (build 22631) |
| Default Python | 3.10.11 (`python`); also 3.12 and 3.14 via `py` launcher |
| pip | 23.0.1 (Python 3.10) |
| GPU | NVIDIA RTX 4070, 12 GB VRAM (~11.5 GB free, ~700 MB used by desktop apps) |
| Driver / CUDA | 596.49 / CUDA 13.2 (driver is forward-compatible with the CUDA 12 runtime wheels) |
| nvcc | 13.2 |
| git | 2.45.1 |
| ffmpeg | 8.0.1 full build (CUDA-enabled) |
| conda | not installed |

**CUDA note:** CTranslate2 4.5+ ships CUDA 12 + cuDNN 9 wheels (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12`) pulled automatically by faster-whisper. The driver (CUDA 13.2)
runs CUDA 12 runtime fine via forward compatibility, so no CUDA downgrade is needed.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Service topology | **Two separate processes, separate venvs, separate ports** | Independent restart, VRAM and dependency isolation; heavy `torch` lives only in the TTS venv (STT/CTranslate2 needs no torch). |
| TTS voice | **CustomVoice predefined speaker + `language="Russian"`** | No reference audio to source/store; deterministic and reproducible. |
| Persistence | **Documented manual start (`.bat` + runbook)** | Auto-start as a Windows service is a later hardening task. |
| Ports | **STT 8001, TTS 8002** | Confirmed with user. |
| Code location | Authored in repo `infra/desktop/`, deployed to Desktop via `scp` | Mirrors existing `infra/pi/` layout. |

## Architecture

```
Pi 5 (Agent Worker)
  ├── STT plugin ── WS ──▶ Desktop 100.75.88.35:8001  /stt   (binary PCM 16k in → JSON text out)
  └── TTS plugin ── WS ──▶ Desktop 100.75.88.35:8002  /tts   (JSON text in → binary PCM 24k out)
```

### Repository layout

```
infra/desktop/
├── README.md              # setup + startup runbook
├── stt/
│   ├── server.py          # FastAPI: WS /stt, GET /health
│   ├── audio.py           # pure helpers: PCM<->float, framing
│   ├── requirements.txt   # faster-whisper, fastapi, uvicorn[standard], numpy, python-dotenv
│   ├── start-stt.bat
│   ├── .env.example
│   └── tests/             # pytest unit tests for audio.py
└── tts/
    ├── server.py          # FastAPI: WS /tts, GET /health
    ├── audio.py           # pure helpers: float->int16 PCM, resample to 24k
    ├── requirements.txt   # torch (cu12x), faster-qwen3-tts, fastapi, uvicorn[standard], soundfile, soxr, numpy, python-dotenv
    ├── start-tts.bat
    ├── .env.example
    └── tests/             # pytest unit tests for audio.py
```

Deploy target on Desktop: `C:\Users\Pavel\voice-agent\desktop\`. Venvs
(`stt\.venv`, `tts\.venv`) and downloaded models live only on the Desktop and are
**not** committed. Model caches use the default Hugging Face cache
(`C:\Users\Pavel\.cache\huggingface`).

## STT service (`/stt`)

### Model
`WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")`, loaded
once in the FastAPI lifespan startup hook and kept warm in VRAM (~1.5–2.5 GB).

### WebSocket protocol
- **Client → Server:** binary frames of 16-bit signed PCM, 16 kHz, mono. A JSON
  text message `{"event":"end"}` flushes the current utterance. `{"event":"reset"}`
  clears the buffer.
- **Server → Client:** JSON `{"text": "...", "is_final": true|false, "partial": "..."}`.
  - Periodic **partial** transcripts (`is_final:false`) are emitted as the audio
    buffer grows (cadence ≈ every 1 s of received audio).
  - A **final** transcript (`is_final:true`) is emitted on `{"event":"end"}`.
- Transcription uses `language="ru"`, `beam_size=1` for partials (latency) and a
  higher-quality pass for the final.

### Health
`GET /health` → `{"status":"ok","service":"stt","model":"large-v3-turbo","device":"cuda","compute_type":"int8_float16","model_loaded":true}`.

## TTS service (`/tts`)

### Model
`FasterQwen3TTS.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")` loaded once
at startup (~3–4 GB VRAM), CustomVoice predefined speaker + `language="Russian"`.

**Open API detail (resolved on the box):** the public faster-qwen3-tts examples show
`generate_voice_clone[_streaming]`; the exact CustomVoice/streaming method name is
confirmed by introspecting the installed package on the Desktop during
implementation. Fallback order if CustomVoice streaming is not exposed:
1. official `qwen-tts` package `generate_custom_voice`,
2. voice-clone (`-Base` model) with a bundled Russian reference clip.

The WebSocket contract below is identical regardless of which backend method is used.

### WebSocket protocol
- **Client → Server:** JSON `{"text": "...", "voice": "default"}`.
- **Server → Client:** a sequence of binary frames (16-bit signed PCM, 24 kHz, mono),
  followed by a final JSON `{"done": true}`.
  - Audio is produced by the package's streaming generator with a small `chunk_size`
    targeting a ~250–300 ms first chunk.
  - Each chunk is converted float32 → int16 PCM and resampled to 24 kHz (via `soxr`)
    if the model's native sample rate differs.

### Health
`GET /health` → `{"status":"ok","service":"tts","model":"...CustomVoice","device":"cuda","speaker":"<name>","language":"Russian","model_loaded":true}`.

## Combined VRAM budget

~2.5 GB (STT) + ~4 GB (TTS) + ~0.7 GB (desktop) ≈ 7.2 GB of 12 GB. Fits with headroom.

## Networking & security

- Services bind to `0.0.0.0` (host configurable via `.env`) so localhost smoke tests
  work; access is constrained by Tailscale + Windows Firewall.
- Inbound Windows Firewall rules added for TCP 8001 and 8002.
- No auth on the WS endpoints in this epic (LAN/Tailscale-only). Auth is out of scope.

## Error handling

- **Startup model-load failure:** logged; `/health` returns `model_loaded:false` and
  HTTP 503; WS connections are rejected with a close frame.
- **Per-request inference error:** server sends a JSON `{"error":"<message>"}` frame
  and keeps the connection open for the next request.
- **Client disconnect:** caught, buffers released, no crash.

## Testing

- **Unit (TDD):** pure helpers in `audio.py` (PCM↔float conversion, int16 framing,
  resample) covered by pytest — no GPU required, runnable on Pi or Desktop.
- **Integration smoke (on Desktop):**
  - `curl http://localhost:8001/health` and `:8002/health`.
  - STT: a WS client streams a sample 16 kHz WAV to `/stt`, sends `{"event":"end"}`,
    prints partial + final transcripts.
  - TTS: a WS client sends Russian text to `/tts`, collects binary PCM, writes a WAV,
    and reports first-chunk latency.

## Out of scope

- Auto-start Windows service / boot persistence (later hardening).
- WS authentication.
- Pi-side Agent Worker plugins that consume these services (Epic 4+).
- True low-latency word-level streaming STT (partials are buffer-cadence based).

## Acceptance criteria (from issues #2 and #3)

- [ ] faster-whisper + CTranslate2 installed on Desktop in a venv; `large-v3-turbo`
      downloaded and loads on CUDA with `int8_float16`.
- [ ] `/stt` WS endpoint: binary PCM 16 kHz in → JSON `{text,is_final,partial}` out,
      Russian (`language="ru"`), with partials + final on `end`.
- [ ] faster-qwen3-tts + Qwen3-TTS-1.7B installed on Desktop in a venv; model loads
      on CUDA.
- [ ] `/tts` WS endpoint: JSON text in → binary PCM 24 kHz out + `{"done":true}`,
      Russian CustomVoice, streaming chunks.
- [ ] `/health` on both services.
- [ ] Startup instructions documented (`infra/desktop/README.md`).
