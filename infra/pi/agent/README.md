# Agent Worker — LiveKit Agents (Pi 5)

The Voice Agent's **Agent Worker**, running on the Pi 5 (Raspberry Pi OS, aarch64,
Python 3.13). It registers with the local LiveKit SFU, is dispatched into rooms
automatically, and runs a full **STT → LLM → TTS conversation loop**: it transcribes
each user turn via the Desktop STT plugin (input audio path), passes the transcript
to a local **LiteLLM proxy** (which forwards to the cloud LLM), and speaks the
reply via the Desktop TTS plugin (output audio path). The LLM is instructed by a
**SOUL personality** (`SOUL.md`) loaded verbatim at startup. Turn detection is
handled by a **local Silero VAD** (`livekit-plugins-silero`, ONNX on the Pi) with
`turn_detection="vad"`.

This is the **Epic-4 issue #13 LiteLLM + LLM conversation + SOUL slice**, building on
the STT/TTS audio pipeline proved in #12 (echo loop) and the TTS greeting scaffolded
in #11. The LiteLLM proxy runs as its **own systemd service** in its own venv (not
inside the agent venv, not in Docker).

On join, the agent speaks a **fixed Russian greeting** (`AGENT_GREETING`) before
entering the conversation loop (greeting-on-join behaviour from #11 is preserved).

| Component | Detail |
| --- | --- |
| Runtime | Python venv at `.venv`, run as `python -m agent start` |
| SFU | `ws://localhost:7880` (local Pi LiveKit SFU) |
| Dispatch | empty `agent_name` → automatic room dispatch |
| STT | custom `DesktopSTT` over `ws://100.75.88.35:8001/stt` (Tailscale) |
| LLM | `livekit-plugins-openai` → local LiteLLM proxy at `:4000/v1` → Ollama Cloud |
| TTS | custom `DesktopTTS` over `ws://100.75.88.35:8002/tts` (Tailscale) |
| VAD | local Silero (`livekit-plugins-silero`), ONNX on Pi, `turn_detection="vad"` |
| Health gates | `http://100.75.88.35:8001/health` (STT) + `:8002/health` (TTS) at startup |
| systemd (worker) | `deploy/voice-agent-worker.service` |
| systemd (LiteLLM) | `deploy/litellm.service` |

## Prerequisites
- **Pi 5**, Raspberry Pi OS (aarch64). Python 3.11+ — verified on **3.13.5**.
- **LiveKit SFU running on the Pi** (Epic 1, `infra/pi/docker-compose.yml`) and
  listening on `:7880`.
- **Desktop STT service reachable over Tailscale** at `100.75.88.35:8001`
  — see `infra/desktop/README.md`.
- **Desktop TTS service reachable over Tailscale** (Epic 3) at `100.75.88.35:8002`
  — see `infra/desktop/README.md`.
- **Ollama Cloud API key** — required for the live conversation; see
  [Ollama Cloud key](#ollama-cloud-key) below.
- **LiteLLM proxy running on the Pi** (`litellm.service`); see
  [LiteLLM proxy setup](#litellm-proxy-setup) below.

## Setup
```bash
cd infra/pi/agent
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in LIVEKIT_API_KEY/SECRET from infra/pi/.env
```

`requirements.txt` pins `livekit-agents==1.6.2` (plus `livekit-plugins-silero`,
`livekit-plugins-openai`, `python-dotenv==1.2.2`, `websockets==16.0`, and pytest for
dev). ARM64 wheels resolve cleanly on the Pi — no torch/GPU deps, since all heavy
inference is remote.

`livekit-plugins-silero` downloads its ONNX VAD model weights on first run (~1–2 MB,
cached locally). A `DeprecationWarning` from this package is expected on the pinned
`1.6.2` and is harmless — the local VAD is deliberate (the framework's cloud-backed
default 401s on a self-hosted Pi; see `agent.py` for details).

## Ollama Cloud key

The LLM backend is **Ollama Cloud** (`nemotron-3-super:cloud`), reached through the
local LiteLLM proxy. The proxy authenticates to Ollama Cloud using an API key that
you must obtain and install manually:

1. Log in at [ollama.com](https://ollama.com) → **Settings** → **API Keys** →
   create a key.
2. Add it to the LiteLLM secrets file (created in [LiteLLM proxy setup](#litellm-proxy-setup)):
   ```
   OLLAMA_API_KEY=<your-key>
   ```

This key is required **only for live conversation** (i.e., any actual LLM call). The
agent's own `.env` does NOT carry it — it lives only in the LiteLLM `EnvironmentFile`
(`/etc/voice-agent/litellm.env`).

## LiteLLM proxy setup

LiteLLM runs as its own systemd service from a **dedicated venv** — separate from the
agent venv, not in Docker. It exposes an OpenAI-compatible endpoint on port 4000 that
the agent's `openai.LLM` plugin calls.

### Install

```bash
# 1. Create a dedicated venv for LiteLLM (separate from the agent venv):
python3 -m venv /opt/voice-agent-litellm/.venv
/opt/voice-agent-litellm/.venv/bin/pip install 'litellm[proxy]'

# 2. Create the EnvironmentFile with secrets (never commit this file):
sudo mkdir -p /etc/voice-agent
sudo tee /etc/voice-agent/litellm.env > /dev/null <<EOF
OLLAMA_API_KEY=<your-ollama-key>
LITELLM_MASTER_KEY=<your-litellm-master-key>
EOF
sudo chmod 600 /etc/voice-agent/litellm.env

# 3. Ensure the config exists at the expected path, e.g.:
#    /opt/voice-agent/infra/pi/litellm/config.yaml

# 4. Edit deploy/litellm.service: replace placeholder paths and set User/Group.
```

### systemd

```bash
sudo cp deploy/litellm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now litellm

# Manage / follow
sudo systemctl restart litellm
journalctl -u litellm -f
```

The unit reads `/etc/voice-agent/litellm.env` via `EnvironmentFile` (chmod 600) for
`OLLAMA_API_KEY` and `LITELLM_MASTER_KEY`. The agent worker unit (`voice-agent-worker.service`)
is ordered `After=litellm.service`, so systemd starts the proxy first.

The config file is at `infra/pi/litellm/config.yaml`. It defines the `voice-agent`
model alias, the Ollama Cloud `api_base`, and reads the `OLLAMA_API_KEY` /
`LITELLM_MASTER_KEY` from environment using LiteLLM's `os.environ/VAR_NAME` syntax.

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
| `STT_LANGUAGE` | BCP-47 tag attached to the returned `SpeechData.language` metadata only — **not** sent to the Desktop STT server. To change the actual recognition language, set `STT_LANGUAGE` on the Desktop STT server (`infra/desktop/stt/.env`) | `ru` |
| `TTS_WS_URL` | Desktop TTS WebSocket (over Tailscale) | `ws://100.75.88.35:8002/tts` |
| `TTS_HEALTH_URL` | Desktop TTS health endpoint (startup gate) | `http://100.75.88.35:8002/health` |
| `TTS_VOICE` | Voice id → server speaker `aiden` (Russian) | `default` |
| `TTS_SAMPLE_RATE` | Rate the plugin labels published frames with; must match the server's output rate | `24000` |
| `LLM_BASE_URL` | LiteLLM proxy OpenAI-compatible endpoint — the `/v1` suffix is **mandatory** for the openai plugin | `http://localhost:4000/v1` |
| `LLM_MODEL` | Model alias as defined in `infra/pi/litellm/config.yaml` | `voice-agent` |
| `LLM_API_KEY` | Bearer key sent to the LiteLLM proxy — must match the proxy's `LITELLM_MASTER_KEY` when one is set | `litellm-local` |
| `SOUL_PATH` | Path to the SOUL personality file loaded as the system prompt | `SOUL.md` next to `agent.py` |
| `AGENT_GREETING` | Literal greeting string spoken on join | `Привет! Я голосовой ассистент. Чем могу помочь?` |
| `AGENT_WORKER_PORT` | Worker's own HTTP server port (the framework default `8081` is taken on the Pi) | `8090` |

- **Reuse the LiveKit keys from `infra/pi/.env`** — the worker MUST use the same
  `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` the SFU loads. A mismatch fails SFU
  registration with an **auth error** (Risk 6).
- **`LLM_BASE_URL` must include `/v1`** — the `livekit-plugins-openai` plugin
  appends paths like `/chat/completions` to this base; omitting `/v1` sends requests
  to the wrong route and returns 404.
- **`LLM_API_KEY` must match `LITELLM_MASTER_KEY`** if the proxy has one set. Both
  live in different files (agent `.env` vs LiteLLM `EnvironmentFile`); keep them in
  sync.
- `STT_SAMPLE_RATE` is the rate the plugin sends audio to the Desktop STT server.
  It MUST match the server's expected input rate (`16000` Hz); a mismatch produces
  garbled or empty transcripts. Keep it at `16000` unless the server contract changes.
- `TTS_SAMPLE_RATE` is the rate the plugin **labels its published audio frames**
  with, and LiveKit resamples from it to the WebRTC rate downstream. It MUST match
  the Desktop TTS server's actual output rate (`24000` Hz, the server contract);
  changing it without the server's output rate also changing produces pitch/speed
  artifacts. Keep it at `24000` unless the server's output rate changes.

## SOUL.md — personality and system prompt

`SOUL.md` (next to `agent.py`, or at `SOUL_PATH`) is loaded **verbatim** as the
LLM system prompt at every session start. It defines the agent's personality — a
Russian-speaking alter-ego named Priney/Pri (Приня) with a direct, opinionated
communication style suited for spoken TTS output.

To change the agent's behaviour, edit `SOUL.md` directly — no code changes needed.
Keep in mind that replies are spoken aloud through TTS, so the SOUL instructs the
LLM to use short, natural spoken sentences with no markdown, emojis, or code
blocks.

The worker **fails loudly** if `SOUL.md` is missing or empty:
- **Missing:** `RuntimeError: SOUL file not found at <path>. Create
  infra/pi/agent/SOUL.md or set SOUL_PATH …`
- **Empty:** `RuntimeError: SOUL file at <path> is empty. It is loaded verbatim as
  the agent's system prompt; populate it …`

(The content of `SOUL.md` is in Russian by design — but this README documenting it
is in English per project convention.)

## Model swap (req #3)

To change the LLM provider or model, edit **one line** in
`infra/pi/litellm/config.yaml`:

```yaml
model: openai/nemotron-3-super:cloud   # change only this line
```

Examples:
- `openai/gpt-4.1` — GPT-4.1 via direct OpenAI API
- `openai/gemini-2.5-flash-preview` — Gemini via direct Google API
- `openai/nemotron-3-super:cloud` — Ollama Cloud (current default)

The `openai/` prefix tells LiteLLM to use the OpenAI-compatible transport. Do NOT
use `ollama_chat/` for Ollama Cloud — that targets a local Ollama daemon and uses a
different wire protocol. The `:cloud` suffix selects the hosted variant.

The agent code (`agent.py`) is **not touched** when swapping models — all routing
goes through the proxy alias `voice-agent`.

## First-audio latency (req #6)

The worker logs LLM and TTS first-token/first-byte latency after each turn via the
`metrics_collected` hook in `agent.py`. Look for `first-audio-latency:` lines in the
worker journal:

```bash
journalctl -u voice-agent-worker -f | grep first-audio-latency
```

Sample output:
```
first-audio-latency: LLM ttft=0.842s
first-audio-latency: TTS ttfb=0.213s
```

`ttft` is LLM time-to-first-token (end of STT → first LLM token arrives);
`ttfb` is TTS time-to-first-byte (LLM output starts → first audio frame arrives
from the Desktop TTS service).

## Barge-in / interruption

### What it does

The user can speak over the agent while it is talking. The agent stops mid-sentence
and handles the new turn — the interrupted speech is not finished and no audio tail
is played.

### How it works (verified behavior)

Interruptions are **enabled by default** in `livekit-agents 1.6.2` — there is no
explicit barge-in code in `agent.py` (see `AgentSession` constructor comment and
ADR-0006). The framework defaults are:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `enabled` | `True` | interruption is on |
| `min_duration` | `0.5 s` | minimum speech before an interruption registers |
| `false_interruption_timeout` | `2.0 s` | silence window before a tentative interruption is committed |

**Sequence on a committed interruption:**

1. The local Silero VAD detects the user speaking over the agent — TTS playout is
   **paused** immediately.
2. The framework waits for the user's final transcript. If the user falls silent
   within `false_interruption_timeout` (2 s) without a transcript, playout **resumes**
   — this guards against a cough or brief noise triggering a false barge-in.
3. Once a transcript commits the interruption, the framework **cancels the active
   TTS task** → `DesktopTTS._run`'s `finally` block closes the `/tts` WebSocket →
   the Desktop TTS server cancels its producer on disconnect (no in-band stop
   message; see ADR-0006) → any locally-buffered or queued audio is dropped via the
   framework's `clear_buffer`. There is no audio tail.
4. Each agent turn opens a **fresh `/tts` socket**, so closing it aborts only that
   turn — the next reply opens a new socket cleanly.

### Tuning

To change `min_duration`, `false_interruption_timeout`, or disable interruptions
entirely, pass `turn_handling` to `AgentSession`:

```python
from livekit.agents.voice import TurnHandlingOptions, InterruptionOptions

session = AgentSession(
    ...,
    turn_handling=TurnHandlingOptions(
        interruption=InterruptionOptions(
            min_duration=0.8,               # seconds of speech required
            false_interruption_timeout=1.5, # seconds of silence before commit
            # enabled=False to disable barge-in entirely
        )
    ),
)
```

Do **not** use the deprecated flat kwargs `allow_interruptions=` /
`min_interruption_*` — they were removed in `livekit-agents 1.6.x`.

### Headphones caveat

Because the agent now responds to heard speech, playing TTS audio through
**speakers** lets the agent hear its own voice and self-interrupt — barge-in makes
this more pronounced than the earlier echo / conversation-loop issues. **Use
headphones** (or mute your mic while the agent speaks). This is already required by
the smoke-test below; the caveat is worth keeping in mind when tuning
`min_duration` (a higher threshold reduces sensitivity to self-echo if headphones
are not available in a particular setup).

## Run
Foreground, dev mode (verbose logs):
```bash
.venv/bin/python -m agent dev
```

Foreground, prod mode:
```bash
.venv/bin/python -m agent start
```

On startup the worker loads `SOUL.md`, then runs the STT and TTS health gates (in
that order), then registers with the SFU. Both gates must pass — an unavailable STT
or TTS service aborts with a clear error before the worker ever joins a room.
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
exist next to the checkout and carry the LiveKit keys + STT + TTS + LLM settings.
The unit is ordered `After=litellm.service` — start LiteLLM first (see
[LiteLLM proxy setup](#litellm-proxy-setup)).

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
Requires the SFU running on the Pi, **both** Desktop services up, the LiteLLM proxy
running (`litellm.service` up with the Ollama key set), and `SOUL.md` present.
This is the **MANUAL on-Pi step** (the acceptance check) — not yet run in CI.

> **Headphones required.** TTS audio plays back into the room via LiveKit. If you
> join without headphones, the agent's spoken reply is captured by the mic and
> fed back into STT, causing a feedback loop. Wear headphones or mute your mic
> while the agent speaks.

1. **Health:** verify both Desktop services respond:
   ```bash
   curl $STT_HEALTH_URL   # 200, model_loaded: true
   curl $TTS_HEALTH_URL   # 200, model_loaded: true
   ```
2. **LiteLLM:** verify the proxy is up and the Ollama key is configured:
   ```bash
   sudo systemctl status litellm
   journalctl -u litellm -n 20
   ```
3. **Start the worker** in the foreground: `.venv/bin/python -m agent dev`.
4. **Confirm registration** with the SFU (worker log + SFU log, see [Run](#run)).
5. **Join the dispatched room** — via the `lk` CLI participant or
   `meet.livekit.io` pointed at the Pi SFU. The agent auto-joins (empty
   `agent_name` → room dispatch).
6. **Hear the Russian greeting.** Verify it is intelligible and not pitch-shifted
   or sped up / slowed. This validates the 24 kHz TTS frame labeling and
   resampling (Risk 2).
7. **Multi-turn conversation test.** Speak a Russian phrase (e.g. "Как тебя зовут?").
   The agent should:
   - Detect the end of your turn via the local Silero VAD.
   - Send the audio to the Desktop STT service (`8001/stt`); the plugin flushes
     with a `{"event":"end"}` message when the VAD closes the turn.
   - Receive the transcript and fire a `user_input_transcribed` event.
   - Call the LiteLLM proxy → Ollama Cloud LLM with the SOUL system prompt;
     the reply is generated in character (Priney/Pri personality, Russian).
   - Speak the LLM reply via TTS — no echo, no verbatim replay.
   Continue with follow-up questions to exercise multi-turn context. Verify:
   - Replies are contextually coherent (LLM sees the full conversation history).
   - Latency is reasonable: check `first-audio-latency:` lines in the worker log
     (`journalctl -u voice-agent-worker -f | grep first-audio-latency`).
   - Personality is consistent with `SOUL.md` (direct, Russian, brief).
8. **Barge-in smoke test.** While the agent is speaking a reply (a long one works
   best — ask it to explain something), start talking over it:
   - Confirm the agent's speech **stops promptly** (within ~0.5 s of your voice
     starting) — no audio tail after you cut in.
   - Confirm your new turn is **transcribed and answered** (the agent processes the
     new utterance and speaks a fresh reply).
   - Ask a very short yes/no question, then stay silent immediately after — confirm
     the agent does **not** interrupt itself (the 2 s `false_interruption_timeout`
     should absorb a brief noise and resume the original reply).
   Check the worker log for a `user_input_transcribed` event on your barge-in turn;
   no error lines should appear around the interruption.
9. **Restart via systemd** (`sudo systemctl restart voice-agent-worker`) and
   confirm it recovers, re-greets on join, and continues the conversation correctly.

Record the smoke result, greeting latency, and LLM+TTS first-audio latency in the
PR / issue.

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
- **SOUL file not found / empty** — `RuntimeError: SOUL file not found at …`. Ensure
  `SOUL.md` exists next to `agent.py`, or set `SOUL_PATH` in `.env` to a readable
  file. The agent cannot start without a populated personality file.
- **LLM 401 / authentication error** — `LLM_API_KEY` in the agent `.env` does not
  match `LITELLM_MASTER_KEY` in `/etc/voice-agent/litellm.env`. Keep them in sync.
  Also check that `litellm.service` is running: `sudo systemctl status litellm`.
- **LLM 404 / not found** — `LLM_BASE_URL` is missing the `/v1` suffix. The default
  is `http://localhost:4000/v1`; verify it is set correctly in `.env`.
- **LLM error: Ollama Cloud auth** — the `OLLAMA_API_KEY` in `/etc/voice-agent/litellm.env`
  is wrong or missing. Verify the key at ollama.com → Settings → API Keys, and that
  the file is chmod 600 and readable by the service account.
- **No transcript / silent after speaking** — check `STT_SAMPLE_RATE` matches
  the Desktop STT server's input rate (`16000`); a mismatch produces empty or garbage
  transcripts. Also verify the Silero VAD model downloaded correctly on first run
  (look for a "Loading Silero VAD" line; a missing model file causes a silent hang).
- **Pitch / speed artifacts in the greeting or reply** — TTS sample-rate mislabel.
  `AudioFrame`s must be 24000 Hz; labeling 24 kHz PCM as 48 kHz plays ~2× fast and
  high-pitched. Keep `TTS_SAMPLE_RATE=24000` (Risk 2).
- **DeprecationWarning from livekit-plugins-silero** — expected on the pinned
  `1.6.2`. Harmless; the local ONNX VAD still functions correctly.
- **API errors after a dependency bump** — `livekit-agents` API surface
  (`AgentSession`, `say`) shifts between minor versions. This worker is pinned to
  `livekit-agents==1.6.2`; do not bump without re-verifying the entrypoint (Risk 9).
