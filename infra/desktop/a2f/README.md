# Desktop A2F Service — Audio2Face-3D → ARKit blendshapes

Epic 8, Phase 1. Runs on the Desktop GPU (RTX 4070) next to STT/TTS. Turns a TTS
utterance (+ the emotion the LLM tagged) into ARKit blendshape frames that the
agent forwards to the browser on the `voiceagent` data channel, where
`arkitToLive2D()` drives the Live2D face (blink/gaze/brows/mouth-form). The mouth
*amplitude* stays on the client volume analyser (ADR-0008) — see the hybrid design
in issue #33 and `docs/research/2026-07-03-a2f-3d-spike.md`.

| Service | Port | Endpoints |
| --- | --- | --- |
| A2F | 8003 | `WS /a2f`, `GET /health` |

## Why a batch-1 engine

The A2F-3D **NIM** needs ~8.8 GB VRAM (a batch-94 cloud engine) and cannot coexist
with STT+TTS on a 12 GB card. Building the engine from the open-source
**Audio2Face-3D-SDK at batch 1** drops it to **~0.3 GB** (measured), coexisting at
~48 % of the card, 1.28 ms/inference, identical ARKit output. That engine is the
backbone of this service. See `build_engine.sh`.

## Architecture

```
Desktop
  TTS service ──(fork PCM16@24k + emotion)──▶ A2F service (this) ──WS /a2f──▶ Agent (Pi) ──▶ voiceagent
                                                   │
                                     ┌─────────────┴──────────────┐
                                     │ engine.py backend          │
                                     │  mock   → synthetic frames │  (default; no GPU)
                                     │  helper → a2f_stream (C++)  │  (batch-1 TRT engine)
                                     └────────────────────────────┘
```

## WS /a2f contract

Client → server, per utterance, in order:
1. `{"emotion": "happy"}` or `{"emotion": [10 floats]}` — optional, default neutral
2. binary PCM16 @ 24 kHz mono frames (the forked TTS audio)
3. `{"end": true}`

Server → client: `{"type":"blendshapes","frame":i,"t":sec,"arkit":{name:value,…}}`
per frame, then `{"done": true}` (or `{"error":"…"}`).

## Backends (`A2F_BACKEND`)

- **`mock`** (default) — synthetic, well-formed blendshapes; no GPU. Lets the WS
  contract, agent wiring, and frontend be built/tested now. `pytest tests/` covers it.
- **`helper`** — the compiled C++ `a2f_stream` helper on the batch-1 TensorRT
  engine (`a2f_stream/`). **Built + verified on the box**: regression geometry → GPU
  blendshape solve → 68 ARKit coefficients/frame at 60 FPS. Remaining work is making
  the helper *persistent* (load the engine once) to avoid per-utterance reload.

## Run / test

```bash
py -3.12 -m venv .venv && .venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python server.py            # A2F_BACKEND=mock by default
# tests (mock, no GPU):
.venv\Scripts\python -m pytest tests/ -q
curl http://100.75.88.35:8003/health
```

## Deploy

NSSM service `voice-agent-a2f` (LocalSystem, boot-start), mirroring STT/TTS — see
`deploy/`. Firewall: `netsh advfirewall firewall add rule name=voiceagent-a2f
dir=in action=allow protocol=TCP localport=8003`.

## Status — PRODUCTION (helper backend live)

The real `helper` backend is deployed as an always-on containerized service on the
Desktop GPU — see [ADR 0015](../../../docs/adr/0015-a2f-helper-production.md) and
[`deploy/`](deploy/) (Dockerfile + `setup_a2f_service.ps1`). The `mock` backend
remains the CI / frontend-dev default when run bare.

- [x] FastAPI service, `/health`, WS `/a2f` contract, emotion mapping, mock backend, tests
- [x] `build_engine.sh` — reproducible batch-1 engine (verified in the spike)
- [x] `a2f_stream/` C++ helper — 68 ARKit coeffs/frame (52 skin+16 tongue), GPU blendshape solve
- [x] `helper` backend subprocess bridge in `engine.py` (resample 24→16k, feed, map, 60→30 fps)
- [x] persistent helper — SDK *Interactive* executors, engine loaded once (issue #34)
- [x] TTS-side PCM fork + `emotion` field; agent-side `/a2f` forward to `voiceagent` (issues #33/#36)
- [x] **production deploy** — Docker image (`voice-agent-a2f:latest`) in the TensorRT
  container, `--restart always`, boot via logon Scheduled Task; VRAM Δ≈0.4 GB
