# Agent Worker scaffold + TTS greeting (output audio path) — implementation plan

**Task:** GitHub issue #11 (sub-issue of Epic 4, #4) — see `docs/plans/0011-agent-scaffold-tts-greeting.md`
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Build on LiveKit `AgentSession`, not a hand-rolled loop

**Decision:** The worker entrypoint constructs a LiveKit `AgentSession` configured with only the custom TTS plugin and speaks the greeting via `session.say(...)`.
**Rationale:** Carried from grilling + ADR-0006 and `CONTEXT.md`; the framework owns track publishing and resampling. Mirrors the framework-native pattern the rest of Epic 4 builds on.
**Alternative:** A hand-rolled `rtc.AudioSource(24000,1)` + manual track publish — kept as a documented fallback (Risk 1) if the installed `livekit-agents` forbids a TTS-only session.

### DD-2: Fresh TTS WebSocket per utterance

**Decision:** The TTS plugin opens a new `websockets.connect(TTS_WS_URL, max_size=None)` per synthesize call and closes it in a `finally`.
**Rationale:** ADR-0006 (close-socket abort semantics for future barge-in); mirrors the reference client `infra/desktop/scripts/smoke_tts.py:11`.
**Alternative:** A long-lived shared socket — rejected; complicates abort and reconnection, not needed for a per-greeting MVP.

### DD-3: Frame PCM at 24 kHz; let LiveKit resample

**Decision:** Wrap PCM16 bytes into `rtc.AudioFrame(sample_rate=24000, num_channels=1)`; declare the plugin sample rate as 24000. No hand-rolled DSP.
**Rationale:** The `/tts` server emits 24 kHz mono PCM16 (`infra/desktop/README.md` contract); LiveKit resamples to the WebRTC 48 kHz. Mislabeling causes pitch/speed artifacts (Risk 2).
**Alternative:** Resample to 48 kHz in the plugin — rejected; duplicates framework capability and risks artifacts.

### DD-4: Health-gate the TTS `/health` at startup, fail loud

**Decision:** Async `GET TTS_HEALTH_URL` (aiohttp) before the worker becomes ready; non-200 / `model_loaded:false` / refused → clear actionable error, abort startup.
**Rationale:** Issue AC + grilling resilience: a worker that can never speak should not silently register.
**Alternative:** Lazy check on first utterance — rejected; defers failure to call time and clips the greeting.

### DD-5: PCM16 odd-byte boundary handling

**Decision:** Buffer a leftover byte across binary chunks so a 2-byte PCM sample is never split across `AudioFrame`s.
**Rationale:** `/tts` chunks are arbitrary byte lengths (Risk 3); splitting a sample corrupts audio.
**Alternative:** Assume even-length chunks — rejected; the contract makes no such guarantee.

### DD-6: Reuse the canonical LiveKit key pair; never commit secrets

**Decision:** Config reads `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` from the agent `.env`; the README documents copying them from `infra/pi/.env`. `.env` is gitignored; only `.env.example` is committed.
**Rationale:** The SFU signs tokens with the same pair (`infra/pi/.env.example` notes the reuse); a mismatch fails registration with an auth error (Risk 6).
**Alternative:** A second key pair — rejected; would break SFU registration.

### DD-7: Mirror the desktop test pattern (fake servers, no hardware)

**Decision:** `tests/conftest.py` provides a fake `/tts` WebSocket server and a fake `/health` HTTP server; tests run CI-friendly with no GPU.
**Rationale:** Mirrors `infra/desktop/tts/tests/conftest.py` (stub the heavy dependency, put the module dir on `sys.path`).
**Alternative:** Integration-only tests against the real Desktop — rejected; not CI-friendly, slow, hardware-bound.

## Tasks

### Task 1: Project skeleton, deps, env template, gitignore

- **Files:** `infra/pi/agent/requirements.txt` (create), `infra/pi/agent/.gitignore` (create), `infra/pi/agent/.env.example` (create)
- **Depends on:** none
- **Scope:** S
- **What:** Create the `infra/pi/agent/` package skeleton with pinned deps, a gitignore, and a documented env template.
- **How:** `requirements.txt`: `livekit-agents` (pin the exact 1.x resolved at install), `python-dotenv`, `websockets`, and dev deps `pytest`, `pytest-asyncio`; comment that `aiohttp` arrives transitively via `livekit-agents`. `.gitignore`: mirror `infra/desktop/.gitignore` (`.venv/`, `__pycache__/`, `*.pyc`, `.env`, `*.wav`, `models/`). `.env.example`: every key from Task 2 with comments — `LIVEKIT_URL=ws://localhost:7880`, empty `LIVEKIT_API_KEY=`/`LIVEKIT_API_SECRET=` (note: copy from `infra/pi/.env`), `TTS_WS_URL=ws://100.75.88.35:8002/tts`, `TTS_HEALTH_URL=http://100.75.88.35:8002/health`, `TTS_VOICE=default`, `TTS_SAMPLE_RATE=24000`, `AGENT_GREETING=Привет! Я голосовой ассистент. Чем могу помочь?`.
- **Context:** `infra/desktop/.gitignore`, `infra/desktop/tts/requirements.txt`, `infra/pi/.env.example`, plan §5–§6.
- **Verify:** `test -f infra/pi/agent/requirements.txt && test -f infra/pi/agent/.gitignore && test -f infra/pi/agent/.env.example`

### Task 2: Config module

- **Files:** `infra/pi/agent/config.py` (create)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Load `.env` via python-dotenv and expose a typed, validated config object.
- **How:** Read `LIVEKIT_URL` (default `ws://localhost:7880`), `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (required — fail fast with a clear message if missing), `TTS_WS_URL`, `TTS_HEALTH_URL`, `TTS_VOICE` (`default`), `TTS_SAMPLE_RATE` (int, `24000`), `AGENT_GREETING` (Russian default). Use a frozen dataclass or simple typed accessor; raise a clear `RuntimeError` naming the missing key.
- **Context:** `infra/desktop/tts/.env.example`, `infra/pi/.env.example`, plan §2, §5.
- **Verify:** `cd infra/pi/agent && python -c "import config"` — imports without error

### Task 3: TTS plugin (core of the slice)

- **Files:** `infra/pi/agent/tts_plugin.py` (create)
- **Depends on:** Task 2
- **Scope:** L
- **What:** Implement a `livekit.agents.tts.TTS` subclass that synthesizes one utterance over a fresh `/tts` WebSocket.
- **How:** Per utterance: open `websockets.connect(TTS_WS_URL, max_size=None)`; send `{"text": text, "voice": voice}`; loop `recv()` — binary → accumulate into a PCM buffer and emit `rtc.AudioFrame(sample_rate=24000, num_channels=1)`, carrying a leftover odd byte across chunks (DD-5); JSON `{"done": true}` → finish; JSON `{"error": ...}` → raise a clear plugin error. Close the socket in `finally` (DD-2). Declare capabilities/sample rate at 24 kHz mono (DD-3). Mirror the recv shape of `infra/desktop/scripts/smoke_tts.py`.
- **Context:** `infra/desktop/scripts/smoke_tts.py`, `infra/desktop/README.md` (`WS /tts` contract), plan §4 Task 3, Risks 2–3.
- **Verify:** `cd infra/pi/agent && python -m pytest tests/test_tts_plugin.py -q` — green (after Task 6)

### Task 4: Health gate

- **Files:** `infra/pi/agent/health.py` (create)
- **Depends on:** Task 2
- **Scope:** S
- **What:** Async startup gate that `GET`s the TTS `/health` and aborts loudly when the service is unavailable.
- **How:** `aiohttp` GET `TTS_HEALTH_URL`. 200 with `model_loaded: true` → return/proceed. Non-200 / connection refused / `model_loaded: false` → raise a clear, actionable error ("Desktop TTS unavailable at <url> — is the service running / Tailscale up?"). Add a short timeout.
- **Context:** `infra/desktop/README.md` (`GET /health`), `infra/desktop/tts/server.py` (health response shape), plan §4 Task 4.
- **Verify:** `cd infra/pi/agent && python -m pytest tests/test_health.py -q` — green (after Task 6)

### Task 5: Worker entrypoint + greeting

- **Files:** `infra/pi/agent/agent.py` (create)
- **Depends on:** Task 3, Task 4
- **Scope:** M
- **What:** Define the LiveKit Agents `entrypoint(ctx)` that runs the health gate, joins the room, and speaks the Russian greeting via the TTS plugin.
- **How:** At process/prewarm start run the health gate (Task 4). In `entrypoint`: connect to the room, construct an `AgentSession` configured with **only** the custom TTS plugin (Task 3), and on participant join speak `AGENT_GREETING` via `session.say(...)` (DD-1). Expose `cli.run_app(WorkerOptions(entrypoint_fnc=...))`. Trigger the greeting on the participant-join event (not connect time) to avoid first-audio clipping (Risk 5). Document the agent/room name. **Verify the TTS-only `say()` path early (Risk 1)**; if the installed version forbids it, fall back to `rtc.AudioSource(24000,1)` + manual track publish and note it.
- **Context:** `infra/pi/agent/tts_plugin.py`, `infra/pi/agent/health.py`, `infra/pi/agent/config.py`, plan §4 Task 5, Risks 1, 4, 5.
- **Verify:** `cd infra/pi/agent && python -c "import agent"` — imports without error

### Task 6: Tests (TTS plugin + health gate)

- **Files:** `infra/pi/agent/tests/conftest.py` (create), `infra/pi/agent/tests/test_tts_plugin.py` (create), `infra/pi/agent/tests/test_health.py` (create)
- **Depends on:** Task 3, Task 4
- **Scope:** L
- **What:** Unit tests with fake `/tts` and `/health` servers, no hardware.
- **How:** `conftest.py`: a fake `/tts` WebSocket server (reads the JSON request, streams N fixed PCM16 chunks then `{"done":true}`; plus error-injecting and disconnect variants) and a fake `/health` HTTP server; put the module dir on `sys.path` (mirror `infra/desktop/tts/tests/conftest.py`). `test_tts_plugin.py`: assert the request JSON sent; emitted `AudioFrame`s are 24 kHz mono with the expected total sample count; odd-byte chunk boundaries handled; `{"error"}` raises; socket closed after each utterance. `test_health.py`: 200+loaded passes; 503 / refused raises the documented error. Use `pytest-asyncio`.
- **Context:** `infra/desktop/tts/tests/conftest.py`, `infra/desktop/tts/tests/test_server.py`, `infra/pi/agent/tts_plugin.py`, `infra/pi/agent/health.py`, plan §7.
- **Verify:** `cd infra/pi/agent && python -m pytest tests/ -q` — all green

### Task 7: systemd unit

- **Files:** `infra/pi/agent/deploy/voice-agent-worker.service` (create)
- **Depends on:** Task 5
- **Scope:** S
- **What:** A systemd unit to run the worker from the venv.
- **How:** `ExecStart` = venv python running `agent.py` in worker mode; `WorkingDirectory=/…/infra/pi/agent`; `EnvironmentFile=/…/infra/pi/agent/.env`; `Restart=on-failure`; `RestartSec`; `StandardOutput=journal`. Use placeholder absolute paths documented in the README.
- **Context:** `infra/pi/agent/agent.py`, plan §4 Task 7.
- **Verify:** `test -f infra/pi/agent/deploy/voice-agent-worker.service` and the unit references the venv python + EnvironmentFile

### Task 8: README runbook

- **Files:** `infra/pi/agent/README.md` (create)
- **Depends on:** Task 5, Task 7
- **Scope:** M
- **What:** Runbook mirroring `infra/desktop/README.md` style.
- **How:** Document venv setup (Python 3.11+), `.env` from `.env.example` and reusing `infra/pi/.env` keys, systemd install/start/logs, the `/health` check, the dispatch + join smoke-test procedure (start worker → dispatch into a room → join via `lk` CLI or `meet.livekit.io` → confirm the Russian greeting plays with no pitch/speed artifacts), the agent/room name, the pinned `livekit-agents` version, and troubleshooting (Risks 1, 4, 6, 7). Note the on-Pi smoke run as a manual step.
- **Context:** `infra/desktop/README.md`, `infra/pi/agent/agent.py`, `infra/pi/agent/deploy/voice-agent-worker.service`, plan §7 manual smoke.
- **Verify:** `test -f infra/pi/agent/README.md` and it documents venv, systemd, and the smoke test

### Task 9: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run full validation: lint, types, tests.
- **How:** Auto-detect from project config; no build step for a Python package.
- **Context:** —
- **Verify:** `cd infra/pi/agent && python -m pytest tests/ -q` — all green; lint (ruff/flake8 if configured) clean

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** The slice has two independent fan-outs on disjoint files (plugin vs health gate; entrypoint vs tests) that parallelize safely; everything else is a dependency chain.
- **Order:**
  Group 1: Task 1
  ─── barrier ───
  Group 2: Task 2
  ─── barrier ───
  Group 3 (parallel): Task 3, Task 4
  ─── barrier ───
  Group 4 (parallel): Task 5, Task 6
  ─── barrier ───
  Group 5: Task 7
  ─── barrier ───
  Group 6: Task 8
  ─── barrier ───
  Group 7: Task 9 (Validation)

## Verification

From issue #11 acceptance criteria:

- [ ] Agent Worker runs from a Pi venv under a systemd unit (start/stop/restart, logs)
- [ ] Worker registers with the LiveKit SFU and joins a room when dispatched
- [ ] Custom TTS plugin connects to the Desktop `/tts` WebSocket and streams PCM16 @24kHz
- [ ] A participant joining the room hears a fixed Russian greeting, correctly resampled (no pitch/speed artifacts)
- [ ] Startup checks TTS `/health`; an unavailable GPU Worker yields a clear logged error, not a silent hang
- [ ] README documents venv setup, the systemd unit, and how to dispatch/join for the smoke test
- [ ] Unit tests for the TTS plugin and health gate pass in CI (no hardware needed)

## Materials

- `docs/plans/0011-agent-scaffold-tts-greeting.md` — full design, risks, effort.
- `infra/desktop/README.md` — `WS /tts` + `GET /health` contracts.
- `infra/desktop/scripts/smoke_tts.py` — reference TTS WebSocket client.
- `infra/desktop/tts/tests/conftest.py` — fake-server/test-stub pattern to mirror.
- `infra/pi/.env` / `infra/pi/.env.example` — canonical LiveKit key pair.
- ADR-0006 (`docs/adr/0006-agent-turn-control.md`), `CONTEXT.md`.
