# Plan 0011 — Agent Worker scaffold + TTS greeting (output audio path)

Implements GitHub issue **#11** (sub-issue of Epic 4, #4).
Branch: `epic-4-agent-worker` (off `main`).

Related docs: `CONTEXT.md` (glossary), `docs/adr/0001-hybrid-architecture.md`,
`docs/adr/0003-websocket-stt-tts.md`, `docs/adr/0006-agent-turn-control.md`,
`infra/desktop/README.md` (TTS contract), `infra/pi/docker-compose.yml` (SFU keys).

> **Status:** plan only — no code written yet. Awaiting review.

---

## 1. Goal and scope

### Goal
Stand up the **Agent Worker** on the Pi 5 and prove the **output audio path**
end-to-end: the worker runs as a service, registers with the LiveKit SFU, joins a
room on dispatch, and speaks a fixed Russian greeting through a custom **TTS
plugin** that talks to the Desktop `/tts` WebSocket. A human joining the room
hears the greeting, correctly resampled.

This is the first tracer bullet of Epic 4 — it isolates deployment, worker
registration, room join, the TTS plugin/protocol, audio publishing, and
resampling, **without** STT, LLM, or interruption.

### In scope
- Python project skeleton for the Agent Worker under `infra/pi/agent/` (venv-based).
- A custom LiveKit **TTS plugin** (`livekit.agents.tts.TTS` adapter) over the
  Desktop `/tts` WebSocket (per `infra/desktop/README.md` contract).
- Worker entrypoint built on LiveKit **AgentSession** that, on participant join,
  speaks a canned Russian greeting via the TTS plugin.
- **Health-gating** of the Desktop TTS `/health` endpoint at startup.
- **systemd** unit + `.env`/`.env.example` config + README runbook.
- Unit tests for the TTS plugin (against a fake `/tts` server) and the health gate.
- A documented manual e2e smoke test (join a room, hear the greeting).

### NOT in scope (later slices)
- STT plugin, mic capture, transcription, turn detection/endpointing → **#12**.
- LLM, LiteLLM proxy, `SOUL.md` system prompt, sentence chunking → **#13**.
  (The greeting here is a **hardcoded string**, not LLM output.)
- Interruption / barge-in (close-socket TTS abort) → **#14**.
- Hermes MCP tools/memory/skills → **#15**.
- TLS/WSS edge (Caddy) — the worker connects to the SFU over plain `ws://`
  on localhost; browser TLS is Epic 1 #9.
- Mid-call resilience (Tailscale drop, reconnect) beyond the startup health gate.

---

## 2. Architecture overview

```
Human (browser / lk CLI)                         Desktop (RTX 4070, Tailscale 100.75.88.35)
   │  WebRTC audio (subscribe)                      ┌───────────────────────────────┐
   ▼                                                │ TTS service  (already on main) │
LiveKit SFU (Pi, :7880) ──── dispatch ────┐         │  WS /tts   :8002               │
                                          │         │  GET /health :8002             │
                                          ▼         └───────────────────────────────┘
                              Agent Worker (Pi, this slice)            ▲
                              ┌──────────────────────────────┐         │ ws:// (Tailscale)
                              │ agent.py  (AgentSession)      │         │
                              │   on join → session.say(RU)   │         │
                              │ tts_plugin.py ───────────────────────────┘
                              │   send {"text","voice"}       │  recv PCM16@24kHz chunks
                              │   wrap chunks → AudioFrame     │  then {"done":true}
                              │   publish to room track        │
                              │ health gate (GET /health)      │
                              └──────────────────────────────┘
```

Flow on a job:
1. Worker process registers with the SFU at `ws://localhost:7880` using the
   canonical LiveKit API key/secret (reused from `infra/pi/.env`).
2. SFU dispatches the worker into a room; the worker connects and creates an
   audio output track (via AgentSession).
3. On participant join, the worker calls the session's "say" path with the
   canned Russian greeting.
4. The TTS plugin opens a **fresh** `/tts` WebSocket to the Desktop, sends
   `{"text": <greeting>, "voice": "default"}`, and reads binary PCM16 @24kHz
   chunks until `{"done": true}` (or `{"error": ...}`), wrapping each chunk as a
   `rtc.AudioFrame` at **24000 Hz mono**.
5. AgentSession publishes the frames; LiveKit handles WebRTC-side resampling to
   48 kHz. The human hears the greeting.
6. The TTS socket is closed when the utterance completes.

Key decisions carried from grilling/ADRs:
- **AgentSession**, not a hand-rolled loop (CONTEXT.md → AgentSession).
- **Fresh TTS socket per turn** (ADR-0006) — here, per greeting.
- **Framework-native resampling** — declare the plugin/track at 24 kHz; do not
  hand-roll DSP. LiveKit resamples to the WebRTC rate.
- **Health-gate** TTS at startup; fail loud, not silent (grilling: resilience).
- **Russian-only** MVP — greeting and voice are Russian.

### TTS WebSocket contract (from `infra/desktop/README.md`)
- Client → Server: JSON `{"text": "...", "voice": "default"}`.
- Server → Client: binary 16-bit PCM, **24 kHz, mono** chunks, then JSON
  `{"done": true}`. Errors arrive as JSON `{"error": "..."}`.
- `voice: "default"` maps to speaker `aiden` (Russian) on the server.
- Reference client: `infra/desktop/scripts/smoke_tts.py` — the plugin mirrors it.

---

## 3. File structure

All new files under `infra/pi/agent/` (mirrors `infra/desktop/<service>/`):

| File | Description |
| --- | --- |
| `infra/pi/agent/agent.py` | Worker entrypoint: builds AgentSession with the TTS plugin, runs the health gate, speaks the greeting on participant join, and exposes the LiveKit `cli.run_app` runner. |
| `infra/pi/agent/tts_plugin.py` | Custom `livekit.agents.tts.TTS` adapter over the Desktop `/tts` WebSocket: opens a per-utterance socket, sends `{text,voice}`, wraps PCM16@24kHz chunks into `AudioFrame`s, handles `done`/`error`/disconnect. |
| `infra/pi/agent/config.py` | Loads + validates config from env (`.env` via python-dotenv): LiveKit URL/keys, TTS WS + health URLs, voice, greeting text, sample rate. |
| `infra/pi/agent/health.py` | Async startup gate: `GET /health` on the TTS service; raises a clear, actionable error if unavailable. |
| `infra/pi/agent/requirements.txt` | Pinned Python deps (livekit-agents, websockets, python-dotenv, pytest). |
| `infra/pi/agent/.env.example` | Documented env template (no secrets committed). |
| `infra/pi/agent/README.md` | Runbook: venv setup, env, systemd install, dispatch + join smoke test, health check, troubleshooting. |
| `infra/pi/agent/deploy/voice-agent-worker.service` | systemd unit: venv python, `WorkingDirectory`, `EnvironmentFile`, `Restart=on-failure`, journald logging. |
| `infra/pi/agent/tests/conftest.py` | Pytest fixtures: a fake `/tts` WebSocket server and a fake `/health` server. |
| `infra/pi/agent/tests/test_tts_plugin.py` | Unit tests for the TTS plugin against the fake server (chunking, framing, done/error, socket close). |
| `infra/pi/agent/tests/test_health.py` | Unit tests for the health gate (200 → pass, 503/refused → clear failure). |
| `infra/pi/agent/.gitignore` | Ignore `.venv/`, `.env`, `__pycache__/` (mirrors `infra/desktop/.gitignore`). |

No edits to existing files are required for this slice. (`infra/pi/.env` already
holds the LiveKit keys; the worker reads them — document this, do not commit it.)

---

## 4. Step-by-step tasks

> Effort estimates are for an experienced dev; total ≈ **3.5–4.5 h** plus model
> download/first-run time on the Desktop (already done).

### Task 1 — Project skeleton + dependencies
- **What:** Create `infra/pi/agent/` with `requirements.txt`, `.gitignore`,
  empty `__init__`-less module files. Pin `livekit-agents` (latest 1.x — pin the
  exact version resolved at install time), `python-dotenv`, `websockets`,
  `pytest` (dev). Note `aiohttp` arrives transitively via `livekit-agents` and is
  used for the `/health` GET.
- **Files:** `requirements.txt`, `.gitignore`.
- **Depends on:** nothing.
- **Effort:** 20 min.

### Task 2 — Config module
- **What:** `config.py` loads `.env` (python-dotenv) and exposes a typed config
  object: `LIVEKIT_URL` (default `ws://localhost:7880`), `LIVEKIT_API_KEY`,
  `LIVEKIT_API_SECRET`, `TTS_WS_URL` (default `ws://100.75.88.35:8002/tts`),
  `TTS_HEALTH_URL` (`http://100.75.88.35:8002/health`), `TTS_VOICE`
  (`default`), `TTS_SAMPLE_RATE` (`24000`), `AGENT_GREETING` (Russian default).
  Fail fast with a clear message if a required key is missing.
- **File:** `config.py`.
- **Depends on:** Task 1.
- **Effort:** 20 min.

### Task 3 — TTS plugin (core of the slice)
- **What:** Implement a subclass of `livekit.agents.tts.TTS`. The synthesize
  path must, per utterance:
  1. Open a **fresh** WebSocket to `TTS_WS_URL` (`websockets.connect(max_size=None)`).
  2. Send `{"text": <text>, "voice": <voice>}`.
  3. Loop receiving messages: binary → accumulate into the PCM buffer and emit
     `AudioFrame`(s); JSON `{"done": true}` → finish; JSON `{"error": ...}` →
     raise a plugin error.
  4. Convert PCM16 bytes → `rtc.AudioFrame(sample_rate=24000, num_channels=1)`,
     handling the **odd-byte boundary** (PCM16 = 2 bytes/sample; carry a leftover
     byte across chunks so a frame never splits a sample).
  5. Close the socket in a `finally` (clean per-utterance teardown).
  Declare the plugin's capabilities/sample rate as 24 kHz mono so the framework
  resamples downstream.
- **File:** `tts_plugin.py`.
- **Depends on:** Tasks 1–2.
- **Effort:** 60–90 min. *(Highest-risk task — see Risks 1–3.)*

### Task 4 — Health gate
- **What:** `health.py` performs an async `GET TTS_HEALTH_URL` at startup
  (aiohttp). 200 with `model_loaded: true` → proceed. Non-200 / connection
  refused / `model_loaded: false` → log a clear, actionable error
  ("Desktop TTS unavailable at <url> — is the service running / Tailscale up?")
  and abort startup (the worker should not register if it can never speak).
- **File:** `health.py`.
- **Depends on:** Task 2.
- **Effort:** 20 min.

### Task 5 — Worker entrypoint + greeting
- **What:** `agent.py` defines the LiveKit Agents `entrypoint(ctx)`:
  run the health gate (Task 4) at process start (prewarm), connect to the room,
  construct an `AgentSession` configured with **only** the custom TTS plugin, and
  on participant join speak `AGENT_GREETING` via the session's "say" path. Expose
  `cli.run_app(WorkerOptions(entrypoint_fnc=...))` so the module runs as a worker
  that registers with the SFU. Configure dispatch so the worker joins the
  smoke-test room (auto-dispatch acceptable for MVP; document the room/agent name).
- **File:** `agent.py`.
- **Depends on:** Tasks 3–4.
- **Effort:** 45–60 min. *(See Risk 4 — AgentSession may require STT/LLM; verify
  the TTS-only `say()` path early, fall back to direct track publish if needed.)*

### Task 6 — Env template + .gitignore
- **What:** Write `.env.example` with every key from Task 2, commented; ensure
  `.gitignore` excludes `.env`, `.venv/`, `__pycache__/`.
- **Files:** `.env.example`, `.gitignore`.
- **Depends on:** Task 2.
- **Effort:** 10 min.

### Task 7 — systemd unit
- **What:** `deploy/voice-agent-worker.service`: `ExecStart` = venv python +
  `agent.py` in worker mode; `WorkingDirectory=/…/infra/pi/agent`;
  `EnvironmentFile=/…/infra/pi/agent/.env`; `Restart=on-failure`;
  `RestartSec`; `StandardOutput=journal`. Document `systemctl --user` vs system
  service choice in the README.
- **File:** `deploy/voice-agent-worker.service`.
- **Depends on:** Task 5.
- **Effort:** 20 min.

### Task 8 — Tests
- **What:** `conftest.py` provides a fake `/tts` WebSocket server (accepts the
  JSON request, streams N fixed PCM16 chunks, then `{"done":true}`; plus an
  error-injecting variant and a disconnect variant) and a fake `/health` HTTP
  server. `test_tts_plugin.py` asserts: correct request JSON sent; emitted
  `AudioFrame`s are 24 kHz mono with the expected total sample count; odd-byte
  chunk boundaries are handled; `{"error"}` raises; the socket is closed after
  each utterance. `test_health.py` asserts 200 passes and 503/refused fails
  with the clear error.
- **Files:** `tests/conftest.py`, `tests/test_tts_plugin.py`, `tests/test_health.py`.
- **Depends on:** Tasks 3–4.
- **Effort:** 45–60 min.

### Task 9 — README runbook + manual e2e smoke
- **What:** Write `README.md` (mirror `infra/desktop/README.md` style): venv
  setup, `.env` from `.env.example`, how the keys reuse `infra/pi/.env`, systemd
  install/start/logs, the `/health` check, and the smoke-test procedure: start
  the worker, dispatch into a room, join via `lk` CLI or `meet.livekit.io`, and
  confirm the Russian greeting plays cleanly (no pitch/speed artifacts). Then
  **run** that smoke test on the Pi and record the result.
- **File:** `README.md`.
- **Depends on:** Tasks 5, 7.
- **Effort:** 30 min + smoke run.

---

## 5. Configuration

### `infra/pi/agent/.env.example`
```dotenv
# LiveKit SFU (worker runs on the Pi, connects over localhost)
LIVEKIT_URL=ws://localhost:7880
# Reuse the canonical pair from infra/pi/.env (same key/secret the SFU loads).
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# Desktop TTS service (over Tailscale)
TTS_WS_URL=ws://100.75.88.35:8002/tts
TTS_HEALTH_URL=http://100.75.88.35:8002/health
TTS_VOICE=default          # -> server speaker "aiden" (Russian)
TTS_SAMPLE_RATE=24000      # server emits 24kHz mono PCM16; do not change

# Agent
AGENT_GREETING=Привет! Я голосовой ассистент. Чем могу помочь?
```

- **Keys:** the worker MUST use the same API key/secret the SFU loads
  (`LIVEKIT_KEYS` in `infra/pi/docker-compose.yml`, sourced from `infra/pi/.env`).
  Document copying them into the agent `.env` (or, later, share one file).
- **SOUL.md:** **not used in this slice.** The greeting is a literal string in
  `AGENT_GREETING`. `SOUL.md` arrives in #13. No SOUL path is configured here.
- **Sample rate:** fixed at 24000 to match the TTS service; the plugin declares
  this so LiveKit resamples to WebRTC's 48 kHz.

---

## 6. Dependencies (Python)

On the **Pi 5** (Raspberry Pi OS, Python 3.11+; create a dedicated venv):

`infra/pi/agent/requirements.txt`:
```text
livekit-agents     # pin exact 1.x version resolved at install
python-dotenv
websockets         # TTS WebSocket client (mirrors desktop smoke_tts.py)
# aiohttp is pulled in transitively by livekit-agents; used for GET /health
# dev / test
pytest
pytest-asyncio
```
- Pin exact versions after the first `pip install` (commit a frozen list or the
  resolved versions inline), since `livekit-agents` API surface (AgentSession,
  `say`) shifts between minor releases.
- No GPU/torch on the Pi — all inference is remote. This keeps the venv light.
- Verify `livekit-agents` provides ARM64 wheels (it is pure-Python + `livekit`
  rtc bindings; confirm the `livekit` native wheel installs on aarch64 — see
  Risk 7).

---

## 7. Testing strategy

### Unit (CI-friendly, no hardware) — pytest + pytest-asyncio
- **TTS plugin** against a **fake `/tts` server** (in `conftest.py`):
  - Sends correct request JSON `{"text","voice"}`.
  - Emits `AudioFrame`s at 24 kHz mono; total samples match the bytes streamed.
  - Handles arbitrary/odd-length binary chunks without splitting a PCM sample.
  - `{"error": ...}` → raises a clear plugin error.
  - Closes the WebSocket after each utterance (assert per-utterance teardown).
- **Health gate** against a fake `/health` server: 200+loaded → pass;
  503 / connection refused → raises the documented startup error.

### Manual e2e smoke (on the Pi, requires SFU + Desktop TTS up)
1. `curl $TTS_HEALTH_URL` → `200`, `model_loaded: true`.
2. Start the worker (foreground first, then via systemd).
3. Confirm it registers with the SFU (worker log + SFU log).
4. Join the room (via `lk` CLI participant or `meet.livekit.io` pointed at the
   Pi) — agent auto-dispatches/joins.
5. **Hear** the Russian greeting; verify it is intelligible and **not**
   pitch-shifted or sped up/slowed (validates the 24 kHz frame labeling /
   resampling).
6. Restart the worker via `systemctl` and confirm it recovers.

Record the smoke result (and first-audio latency if easy) in the PR/issue.

---

## 8. Acceptance criteria (matches issue #11)

- [ ] Agent Worker runs from a Pi venv under a systemd unit (start/stop/restart, logs).
- [ ] Worker registers with the LiveKit SFU and joins a room when dispatched.
- [ ] Custom TTS plugin connects to the Desktop `/tts` WebSocket and streams PCM16 @24 kHz.
- [ ] A participant joining the room hears a fixed Russian greeting, correctly resampled (no pitch/speed artifacts).
- [ ] Startup checks TTS `/health`; an unavailable GPU Worker yields a clear logged error, not a silent hang.
- [ ] README documents venv setup, the systemd unit, and how to dispatch/join for the smoke test.
- [ ] Unit tests for the TTS plugin and health gate pass in CI (no hardware needed).

---

## 9. Risks and pitfalls

1. **AgentSession may require STT + LLM to construct.** A greeting-only session
   is unusual. *Mitigation:* prefer `session.say(greeting)` (TTS-only playback
   without LLM); if the installed version forbids a TTS-only session, fall back
   to the lower-level path — create an `rtc.AudioSource(24000, 1)`, publish a
   track, and feed it the TTS plugin's frames directly. **Verify in Task 5
   before building everything around AgentSession.**
2. **Sample-rate mislabeling → pitch/speed artifacts.** `AudioFrame` must be
   created at **24000 Hz**. Labeling 24 kHz PCM as 48 kHz makes it play ~2× fast
   and high-pitched. The "no artifacts" acceptance check catches this.
3. **PCM16 frame boundary.** `/tts` chunks are arbitrary byte lengths; 16-bit
   samples are 2 bytes. Converting a chunk with an odd byte count, or splitting a
   sample across `AudioFrame`s, corrupts audio. Buffer a leftover byte across
   chunks.
4. **Worker dispatch / room targeting.** Default LiveKit agent dispatch may not
   join the exact smoke-test room as expected. Document the agent name / dispatch
   rule and the room used; confirm via SFU logs.
5. **First-audio clipping.** Speaking before the participant has subscribed to the
   track clips the start of the greeting. Use the session's join/participant
   event to trigger `say()`, not connection time alone.
6. **LiveKit key mismatch.** If the agent's API key/secret differ from the SFU's
   `LIVEKIT_KEYS`, registration fails with an auth error. Reuse `infra/pi/.env`.
7. **ARM64 wheels for `livekit`.** The `livekit` rtc native bindings must have an
   aarch64 wheel for the Pi's Python. Verify at install; if missing, pin a
   version that ships aarch64 wheels (or build deps). This is a go/no-go for the
   whole epic — surface early.
8. **Desktop reachability.** TTS is remote over Tailscale; the health gate covers
   "down at startup," but a mid-greeting drop is out of scope here (handled by
   resilience work, not this slice). Note it, don't build it.
9. **`livekit-agents` API churn.** AgentSession / `say` signatures move between
   minor versions. Pin the exact version and write the plugin against that
   version's docs; note the version in the README.

---

## 10. Effort summary

| Task | Effort |
| --- | --- |
| 1. Skeleton + deps | 20 min |
| 2. Config module | 20 min |
| 3. TTS plugin | 60–90 min |
| 4. Health gate | 20 min |
| 5. Entrypoint + greeting | 45–60 min |
| 6. Env template + gitignore | 10 min |
| 7. systemd unit | 20 min |
| 8. Tests | 45–60 min |
| 9. README + smoke run | 30 min + smoke |
| **Total** | **≈ 3.5–4.5 h** (+ on-Pi smoke / first-run) |

First task on implementation start: **spike Risk 1 and Risk 7** (TTS-only
AgentSession + `livekit` aarch64 install) — both are go/no-go and cheap to check.
```
