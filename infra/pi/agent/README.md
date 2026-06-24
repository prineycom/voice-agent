# Agent Worker — LiveKit Agents (Pi 5)

The Voice Agent's **Agent Worker**, running on the Pi 5 (Raspberry Pi OS, aarch64,
Python 3.13). It registers with the local LiveKit SFU, is dispatched into rooms
automatically, and speaks a fixed Russian greeting through a custom TTS plugin
that talks to the Desktop `/tts` WebSocket over Tailscale.

This is the **Epic-4 output-audio scaffold** (issue #11): a **TTS-only**
`AgentSession` that proves the output audio path end-to-end (worker registration,
room join, TTS plugin/protocol, audio publishing, 24 kHz resampling). It speaks a
**greeting only** — there is **no STT and no LLM yet** (those land in later slices,
#12/#13).

| Component | Detail |
| --- | --- |
| Runtime | Python venv at `.venv`, run as `python -m agent start` |
| SFU | `ws://localhost:7880` (local Pi LiveKit SFU) |
| Dispatch | empty `agent_name` → automatic room dispatch |
| TTS | custom `DesktopTTS` over `ws://100.75.88.35:8002/tts` (Tailscale) |
| Health gate | `http://100.75.88.35:8002/health` at startup |
| systemd | `deploy/voice-agent-worker.service` |

## Prerequisites
- **Pi 5**, Raspberry Pi OS (aarch64). Python 3.11+ — verified on **3.13.5**.
- **LiveKit SFU running on the Pi** (Epic 1, `infra/pi/docker-compose.yml`) and
  listening on `:7880`.
- **Desktop TTS service reachable over Tailscale** (Epic 3) at `100.75.88.35:8002`
  — see `infra/desktop/README.md`.

## Setup
```bash
cd infra/pi/agent
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in LIVEKIT_API_KEY/SECRET from infra/pi/.env
```

`requirements.txt` pins `livekit-agents==1.6.2` (plus `python-dotenv==1.2.2`,
`websockets==16.0`, and pytest for dev). ARM64 wheels resolve cleanly on the Pi —
no torch/GPU deps, since all inference is remote.

## Configuration
Copy `.env.example` to `.env` and fill in the keys. Every key:

| Key | Meaning | Default |
| --- | --- | --- |
| `LIVEKIT_URL` | SFU URL the worker registers with (local) | `ws://localhost:7880` |
| `LIVEKIT_API_KEY` | LiveKit API key — **must match the SFU's** | _(empty; copy from `infra/pi/.env`)_ |
| `LIVEKIT_API_SECRET` | LiveKit API secret — **must match the SFU's** | _(empty; copy from `infra/pi/.env`)_ |
| `TTS_WS_URL` | Desktop TTS WebSocket (over Tailscale) | `ws://100.75.88.35:8002/tts` |
| `TTS_HEALTH_URL` | Desktop TTS health endpoint (startup gate) | `http://100.75.88.35:8002/health` |
| `TTS_VOICE` | Voice id → server speaker `aiden` (Russian) | `default` |
| `TTS_SAMPLE_RATE` | Rate the plugin labels published frames with; must match the server's output rate | `24000` |
| `AGENT_GREETING` | Literal greeting string spoken on join | `Привет! Я голосовой ассистент. Чем могу помочь?` |

- **Reuse the LiveKit keys from `infra/pi/.env`** — the worker MUST use the same
  `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` the SFU loads. A mismatch fails SFU
  registration with an **auth error** (Risk 6).
- The greeting is a **literal `AGENT_GREETING` string**. There is no `SOUL.md` and
  no LLM in this slice — those arrive in a later slice (#13).
- `TTS_SAMPLE_RATE` is the rate the plugin **labels its published audio frames**
  with, and LiveKit resamples from it to the WebRTC rate downstream. It MUST match
  the Desktop TTS server's actual output rate (`24000` Hz, the server contract);
  changing it without the server's output rate also changing produces pitch/speed
  artifacts. Keep it at `24000` unless the server's output rate changes.

## Run
Foreground, dev mode (verbose logs):
```bash
.venv/bin/python -m agent dev
```

Foreground, prod mode:
```bash
.venv/bin/python -m agent start
```

On startup the worker runs the TTS health gate, then registers with the SFU.
Confirm registration in **both** logs:
- **Worker log:** a "registered worker" line (and, on a job, "Participant … joined;
  speaking greeting.").
- **SFU log:** `docker compose -f infra/pi/docker-compose.yml logs -f livekit`
  shows the agent worker connecting/registering.

## systemd
A unit is provided at `deploy/voice-agent-worker.service`. It runs the worker in
prod mode from the venv. The unit ships with **placeholder paths**
(`/opt/voice-agent/infra/pi/agent`) and a commented-out `User`/`Group`.

```bash
# 1. Edit the unit: replace every /opt/voice-agent/... path with the real
#    checkout location, and set User=/Group= to the account that owns the
#    checkout + venv (a non-root service account).
sudo cp deploy/voice-agent-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now voice-agent-worker

# Manage / follow
sudo systemctl restart voice-agent-worker
journalctl -u voice-agent-worker -f
```

The unit reads `<WorkingDirectory>/.env` via `EnvironmentFile`, so the `.env` must
exist next to the checkout and carry the LiveKit keys + TTS settings.

## Health check
The Desktop TTS service must be up before the worker can speak:
```bash
curl http://100.75.88.35:8002/health      # expect 200 + {"model_loaded": true, ...}
```
The worker runs this same gate at **startup** (`health.py`) and **aborts loudly**
if it fails — it never registers as a mute agent. A failure means the Desktop TTS
service or Tailscale is down (Risk 8).

## Manual e2e smoke test
Requires the SFU running on the Pi and the Desktop TTS service up. This is the
**MANUAL on-Pi step** (the acceptance check) — not yet run in CI.

1. **Health:** `curl $TTS_HEALTH_URL` → `200`, `model_loaded: true`.
2. **Start the worker** in the foreground first: `.venv/bin/python -m agent dev`.
3. **Confirm registration** with the SFU (worker log + SFU log, see [Run](#run)).
4. **Join the dispatched room** — via the `lk` CLI participant or
   `meet.livekit.io` pointed at the Pi SFU. The agent auto-joins (empty
   `agent_name` → room dispatch).
5. **Hear the Russian greeting.** Verify it is intelligible and **not**
   pitch-shifted or sped up / slowed. This validates the 24 kHz frame labeling and
   resampling (Risk 2).
6. **Restart via systemd** (`sudo systemctl restart voice-agent-worker`) and
   confirm it recovers and speaks again.

Record the smoke result and first-audio latency in the PR / issue.

## Tests
Unit tests run against fake `/tts` and `/health` servers — no GPU, no SFU:
```bash
cd infra/pi/agent
.venv/bin/python -m pytest tests/ -q
```
Currently **7 passing** (TTS plugin chunking/framing/error handling + health gate).

## Troubleshooting
- **Auth error on registration** — the agent's `LIVEKIT_API_KEY` /
  `LIVEKIT_API_SECRET` differ from the SFU's. Reuse the canonical pair from
  `infra/pi/.env` (Risk 6).
- **Health gate aborts startup** — `Desktop TTS unavailable …`. The Desktop TTS
  service or Tailscale is down. Check `curl $TTS_HEALTH_URL` and the Desktop
  service (Risk 8).
- **Pitch / speed artifacts in the greeting** — sample-rate mislabel. `AudioFrame`s
  must be 24000 Hz; labeling 24 kHz PCM as 48 kHz plays ~2× fast and high-pitched.
  Keep `TTS_SAMPLE_RATE=24000` (Risk 2).
- **API errors after a dependency bump** — `livekit-agents` API surface
  (`AgentSession`, `say`) shifts between minor versions. This worker is pinned to
  `livekit-agents==1.6.2`; do not bump without re-verifying the entrypoint (Risk 9).
