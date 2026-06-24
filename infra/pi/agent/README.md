# Agent Worker — LiveKit Agents (Pi 5)

The Voice Agent's **Agent Worker**, running on the Pi 5 (Raspberry Pi OS, aarch64,
Python 3.13). It registers with the local LiveKit SFU, is dispatched into rooms
automatically, and runs an **STT → echo → TTS loop**: it transcribes each user turn
via the Desktop STT plugin (input audio path), then speaks it back verbatim via the
Desktop TTS plugin (output audio path). Turn detection is handled by a **local
Silero VAD** (`livekit-plugins-silero`, ONNX on the Pi) with `turn_detection="vad"`.

This is the **Epic-4 echo-loop scaffold** (issue #12): both audio paths are proved
end-to-end (STT protocol, TTS protocol, LiveKit VAD turn detection, audio
publish/subscribe). There is **no LLM** in the loop — the agent echoes the final
transcript verbatim. LLM reasoning lands in a later slice (#13).

On join, the agent also speaks a **fixed Russian greeting** before entering the echo
loop (greeting-on-join behaviour from #11 is preserved).

| Component | Detail |
| --- | --- |
| Runtime | Python venv at `.venv`, run as `python -m agent start` |
| SFU | `ws://localhost:7880` (local Pi LiveKit SFU) |
| Dispatch | empty `agent_name` → automatic room dispatch |
| STT | custom `DesktopSTT` over `ws://100.75.88.35:8001/stt` (Tailscale) |
| TTS | custom `DesktopTTS` over `ws://100.75.88.35:8002/tts` (Tailscale) |
| VAD | local Silero (`livekit-plugins-silero`), ONNX on Pi, `turn_detection="vad"` |
| Health gates | `http://100.75.88.35:8001/health` (STT) + `:8002/health` (TTS) at startup |
| systemd | `deploy/voice-agent-worker.service` |

## Prerequisites
- **Pi 5**, Raspberry Pi OS (aarch64). Python 3.11+ — verified on **3.13.5**.
- **LiveKit SFU running on the Pi** (Epic 1, `infra/pi/docker-compose.yml`) and
  listening on `:7880`.
- **Desktop STT service reachable over Tailscale** at `100.75.88.35:8001`
  — see `infra/desktop/README.md`.
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

`requirements.txt` pins `livekit-agents==1.6.2` (plus `livekit-plugins-silero`,
`python-dotenv==1.2.2`, `websockets==16.0`, and pytest for dev). ARM64 wheels
resolve cleanly on the Pi — no torch/GPU deps, since all heavy inference is remote.

`livekit-plugins-silero` downloads its ONNX VAD model weights on first run (~1 MB,
cached locally). A `DeprecationWarning` from this package is expected on the pinned
`1.6.2` and is harmless — the local VAD is deliberate (the framework's cloud-backed
default 401s on a self-hosted Pi; see `agent.py` for details).

## Configuration
Copy `.env.example` to `.env` and fill in the keys. Every key:

| Key | Meaning | Default |
| --- | --- | --- |
| `LIVEKIT_URL` | SFU URL the worker registers with (local) | `ws://localhost:7880` |
| `LIVEKIT_API_KEY` | LiveKit API key — **must match the SFU's** | _(empty; copy from `infra/pi/.env`)_ |
| `LIVEKIT_API_SECRET` | LiveKit API secret — **must match the SFU's** | _(empty; copy from `infra/pi/.env`)_ |
| `STT_WS_URL` | Desktop STT WebSocket (over Tailscale) | `ws://100.75.88.35:8001/stt` |
| `STT_HEALTH_URL` | Desktop STT health endpoint (startup gate) | `http://100.75.88.35:8001/health` |
| `STT_SAMPLE_RATE` | Sample rate the plugin sends audio at; must match the Desktop STT server's expected input rate | `16000` |
| `STT_LANGUAGE` | Language hint passed to the STT server (BCP-47 code) | `ru` |
| `TTS_WS_URL` | Desktop TTS WebSocket (over Tailscale) | `ws://100.75.88.35:8002/tts` |
| `TTS_HEALTH_URL` | Desktop TTS health endpoint (startup gate) | `http://100.75.88.35:8002/health` |
| `TTS_VOICE` | Voice id → server speaker `aiden` (Russian) | `default` |
| `TTS_SAMPLE_RATE` | Rate the plugin labels published frames with; must match the server's output rate | `24000` |
| `AGENT_GREETING` | Literal greeting string spoken on join | `Привет! Я голосовой ассистент. Чем могу помочь?` |
| `AGENT_WORKER_PORT` | Worker's own HTTP server port (the framework default `8081` is taken on the Pi) | `8090` |

- **Reuse the LiveKit keys from `infra/pi/.env`** — the worker MUST use the same
  `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` the SFU loads. A mismatch fails SFU
  registration with an **auth error** (Risk 6).
- The greeting is a **literal `AGENT_GREETING` string**. There is no `SOUL.md` and
  no LLM in this slice — those arrive in a later slice (#13).
- `STT_SAMPLE_RATE` is the rate the plugin sends audio to the Desktop STT server.
  It MUST match the server's expected input rate (`16000` Hz); a mismatch produces
  garbled or empty transcripts. Keep it at `16000` unless the server contract changes.
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

On startup the worker runs the STT and TTS health gates (in that order), then
registers with the SFU. Both gates must pass — an unavailable STT or TTS service
aborts with a clear error before the worker ever joins a room.
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
exist next to the checkout and carry the LiveKit keys + STT + TTS settings.

## Health check
Both Desktop services must be up before the worker can join a room:
```bash
curl http://100.75.88.35:8001/health      # STT — expect 200 + {"model_loaded": true, ...}
curl http://100.75.88.35:8002/health      # TTS — expect 200 + {"model_loaded": true, ...}
```
The worker runs **both** gates at **startup** (`health.py`) and **aborts loudly** if
either fails — it never registers as a deaf or mute agent. A failure means the
corresponding Desktop service or Tailscale is down (Risk 8). The STT gate is checked
first; a TTS failure after a passing STT gate will still abort with a clear log line.

## Manual e2e smoke test
Requires the SFU running on the Pi, and **both** Desktop services up. This is the
**MANUAL on-Pi step** (the acceptance check) — not yet run in CI.

1. **Health:** verify both services respond:
   ```bash
   curl $STT_HEALTH_URL   # 200, model_loaded: true
   curl $TTS_HEALTH_URL   # 200, model_loaded: true
   ```
2. **Start the worker** in the foreground: `.venv/bin/python -m agent dev`.
3. **Confirm registration** with the SFU (worker log + SFU log, see [Run](#run)).
4. **Join the dispatched room** — via the `lk` CLI participant or
   `meet.livekit.io` pointed at the Pi SFU. The agent auto-joins (empty
   `agent_name` → room dispatch).
5. **Hear the Russian greeting.** Verify it is intelligible and **not**
   pitch-shifted or sped up / slowed. This validates the 24 kHz TTS frame labeling
   and resampling (Risk 2).
6. **Echo-loop test.** Speak a Russian phrase (e.g. "Как тебя зовут?"). The agent
   should:
   - Detect the end of your turn via the local Silero VAD.
   - Send the audio to the Desktop STT service (`8001/stt`); the plugin flushes with
     a `{"event":"end"}` message when the VAD closes the turn.
   - Receive the transcript and fire a `user_input_transcribed` event with
     `is_final=True`.
   - Speak the transcript back verbatim via TTS — hands-free, within one turn, no
     LLM involved.
   Verify the spoken echo is audibly correct and latency is reasonable.
7. **Restart via systemd** (`sudo systemctl restart voice-agent-worker`) and
   confirm it recovers, re-greets on join, and echoes correctly.

Record the smoke result, greeting latency, and echo round-trip latency in the PR /
issue.

## Tests
Unit tests run against fake `/tts` and `/health` servers — no GPU, no SFU:
```bash
cd infra/pi/agent
.venv/bin/python -m pytest tests/ -q
```
Currently passing: TTS plugin (chunking/framing/error handling), STT plugin, and
dual health gate tests.

## Troubleshooting
- **Auth error on registration** — the agent's `LIVEKIT_API_KEY` /
  `LIVEKIT_API_SECRET` differ from the SFU's. Reuse the canonical pair from
  `infra/pi/.env` (Risk 6).
- **STT health gate aborts startup** — `Desktop STT unavailable …`. The Desktop STT
  service or Tailscale is down. Check `curl $STT_HEALTH_URL` and the Desktop
  service (Risk 8).
- **TTS health gate aborts startup** — `Desktop TTS unavailable …`. The Desktop TTS
  service or Tailscale is down. Check `curl $TTS_HEALTH_URL` and the Desktop
  service (Risk 8).
- **No transcript / echo / silent after speaking** — check `STT_SAMPLE_RATE` matches
  the Desktop STT server's input rate (`16000`); a mismatch produces empty or garbage
  transcripts. Also verify the Silero VAD model downloaded correctly on first run
  (look for a "Loading Silero VAD" line; a missing model file causes a silent hang).
- **Pitch / speed artifacts in the greeting or echo** — TTS sample-rate mislabel.
  `AudioFrame`s must be 24000 Hz; labeling 24 kHz PCM as 48 kHz plays ~2× fast and
  high-pitched. Keep `TTS_SAMPLE_RATE=24000` (Risk 2).
- **DeprecationWarning from livekit-plugins-silero** — expected on the pinned
  `1.6.2`. Harmless; the local ONNX VAD still functions correctly.
- **API errors after a dependency bump** — `livekit-agents` API surface
  (`AgentSession`, `say`) shifts between minor versions. This worker is pinned to
  `livekit-agents==1.6.2`; do not bump without re-verifying the entrypoint (Risk 9).
