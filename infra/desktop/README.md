# Desktop GPU Services — STT & TTS

GPU inference services for the Voice Agent, running on the Desktop (Windows 11,
RTX 4070 12GB). Reached from the Pi over Tailscale at `100.75.88.35`.

| Service | Port | Endpoint(s) | Model |
| --- | --- | --- | --- |
| STT | 8001 | `WS /stt`, `GET /health` | faster-whisper `large-v3-turbo` (int8_float16) |
| TTS | 8002 | `WS /tts`, `GET /health` | Qwen3-TTS-12Hz-1.7B (multi-engine: CustomVoice/Base/VoiceDesign, Russian) |

Each service has its own venv. Deploy target: `E:\voice-agent\desktop\`.

## Prerequisites (verified 2026-06-21)
- Windows 11, NVIDIA driver 596.49 (CUDA 13.2), RTX 4070 12GB.
- Python 3.12 via the `py` launcher; ffmpeg on PATH.

## Setup

### STT
```bat
cd E:\voice-agent\desktop\stt
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

### TTS
```bat
cd E:\voice-agent\desktop\tts
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
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
E:\voice-agent\desktop\stt\start-stt.bat

:: TTS  (downloads Qwen3-TTS-1.7B on first run)
E:\voice-agent\desktop\tts\start-tts.bat
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
cd E:\voice-agent\desktop\scripts
..\tts\.venv\Scripts\python smoke_tts.py "Привет! Это тест." tts_out.wav

:: STT: WAV -> transcript (16kHz mono input)
ffmpeg -y -i tts_out.wav -ar 16000 -ac 1 -sample_fmt s16 stt_in.wav
..\stt\.venv\Scripts\python smoke_stt.py stt_in.wav
```

## Configuration
Per-service `.env` (see `.env.example`). Notable keys: `STT_COMPUTE_TYPE`
(`int8_float16`, fall back to `float16`), `TTS_ENGINE`, `TTS_MODEL`, `TTS_SPEAKER`,
`TTS_CHUNK_SIZE` (lower = lower first-chunk latency).

### TTS engines (Qwen3-TTS is multi-engine)

`TTS_ENGINE` selects the synthesis strategy; each engine loads its own model and
interprets the `voice` field in the worker→TTS request `{"text","voice"}`
differently. Switching = change `TTS_ENGINE` (+ engine-specific vars) and restart.

| `TTS_ENGINE`   | Model                          | `voice` means                          | Needs |
| -------------- | ------------------------------ | -------------------------------------- | ---- |
| `custom_voice` | `…-1.7B-CustomVoice` (default)  | speaker id (`aiden`/`ryan`/…), `default`→`TTS_SPEAKER` | nothing extra |
| `voice_clone`  | `…-1.7B-Base`                   | ref profile name, `default`→first; clone a voice from `TTS_REF_AUDIO`+`TTS_REF_TEXT` (or named `TTS_VOICE_REFS`) | a ~15s WAV + its verbatim transcript |
| `voice_design` | `…-1.7B-VoiceDesign`            | instruction string, `default`→`TTS_INSTRUCT` (e.g. "warm male baritone") | a one-line description |

Adding a new engine = new `TTSEngine` subclass + one line in `Engines.REGISTRY`
(`tts/engines.py`). `server.py` and the wire protocol stay untouched. See
`tts/engines.py` and `tts/.env.example` for the full options.

## Notes
- Persistence is manual start for now; auto-start as a Windows service is a later task.
- No auth on the WebSocket endpoints — access is constrained to Tailscale + Windows Firewall.
