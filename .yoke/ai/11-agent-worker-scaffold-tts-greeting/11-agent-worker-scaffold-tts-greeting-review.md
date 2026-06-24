# Code Review: 11-agent-worker-scaffold-tts-greeting

## Summary

### Context and goal

Issue #11 is the Epic-4 output-audio scaffold: a TTS-only LiveKit Agent Worker on the Pi 5 that registers with the local SFU, is auto-dispatched to rooms, runs a startup health gate against the Desktop TTS service, then speaks one fixed Russian greeting through a custom WebSocket TTS plugin. No STT, no LLM yet. The deliverable is the package under `infra/pi/agent/`. All claims were verified against the installed `livekit-agents==1.6.2` API and the real Desktop `/tts` + `/health` contracts.

### Key code areas for review

1. **`tts_plugin.py:ChunkedStream._run()`** — PCM16 odd-byte carry, fresh socket per utterance, error frame → `APIError`, `AudioEmitter.initialize/push/flush`. Verified correct against the installed API.
2. **`health.py:check_tts_health()`** — error classification (refused/timeout/non-200/model-not-loaded) + actionable message; matches the real `/health` shape (200+`model_loaded:true` / 503).
3. **`agent.py:entrypoint()`** — health gate BEFORE `ctx.connect()`, TTS-only `AgentSession`, greeting gated on `wait_for_participant()`.
4. **`config.py:load_config()`** — fail-fast on missing LiveKit keys, int parse of `TTS_SAMPLE_RATE`.
5. **`tests/conftest.py`** — real fake `/tts` (websockets) and `/health` (aiohttp) servers on ephemeral ports; no mocks.

### Complex decisions

1. **TTS-only `AgentSession` + `say()`** (`agent.py:53-70`) — Verified on 1.6.2: constructs with only a TTS provider; `say()` blocks until playout. No `rtc.AudioSource` fallback needed.
2. **Explicit credential passing in `__main__`** (`agent.py:76-85`) — the livekit CLI does not load `.env`; `WorkerOptions` falls back to `LIVEKIT_*` env. Passing creds from the dotenv-loaded config makes a manual foreground run work, not just systemd (which injects `EnvironmentFile`).
3. **`TTS_SAMPLE_RATE` wired through** (`config.py` → `agent.py` → `tts_plugin.py`) — the validated config value now actually labels the published frames; must match the server's 24 kHz output.

### Questions for the reviewer

1. The manual on-Pi e2e smoke (SFU registration, real participant join, greeting playout, latency, systemd restart) is unrun — acceptable to defer per the plan?
2. Worker HTTP port left at the framework default (dev=random / prod=8081) — confirm no collision with other Pi services in the real deployment.

### Risks and impact

- The plugin / health / config layer is well-tested against real sockets; correctness risk is low.
- Residual risk is the untested live path (SFU registration, real audio playout) — an explicit manual step.

### Tests and manual checks

**Auto-tests:** 8 passing — request JSON + 24 kHz framing, odd-byte carry, error frame → `APIError`, fresh-socket-per-utterance, mid-stream disconnect → `ConnectionClosed`, health ok/degraded/refused. Real ephemeral-port servers, proper teardown, no observed flakiness.

**Manual scenarios:**
1. `curl $TTS_HEALTH_URL` → 200 + `model_loaded:true`.
2. Start the worker (foreground), confirm SFU registration.
3. Join the room → hear the Russian greeting, no pitch/speed artifacts.
4. `systemctl restart` → recovers.

### Out of scope

- All `infra/desktop/**` (prior epics, already merged).
- STT/LLM (slices #12 / #13).

## Commits

| Hash    | Description |
| ------- | ----------- |
| 25e7189 | docs(...): add implementation plan |
| 546eba9 | chore(...): scaffold agent worker package and deps |
| e04ec1b | feat(...): add agent worker config module |
| b91f500 | feat(...): add TTS health startup gate |
| de2def3 | feat(...): add TTS WebSocket plugin |
| 76a3a39 | feat(...): add worker entrypoint with TTS greeting |
| ebdd8bb | test(...): add TTS plugin and health gate tests |
| ba4ab49 | chore(...): add systemd unit for the worker |
| 79e5dc0 | docs(...): add agent worker README runbook |
| e38cc88 | docs(...): add execution report |
| ee4fbf2 | fix(...): fix 4 review issues |

## Changed Files

| File | +/- | Description |
| ---- | --- | ----------- |
| infra/pi/agent/config.py | +62 | `AgentConfig` + `load_config()` (fail-fast) |
| infra/pi/agent/tts_plugin.py | +107 | `DesktopTTS` plugin (24 kHz, odd-byte carry); sample-rate now configurable |
| infra/pi/agent/health.py | +60 | `check_tts_health()` startup gate |
| infra/pi/agent/agent.py | +86 | TTS-only `AgentSession` entrypoint; explicit creds in `__main__` |
| infra/pi/agent/tests/conftest.py | +131 | Fake `/tts` + `/health` servers |
| infra/pi/agent/tests/test_tts_plugin.py | +128 | Plugin behavior incl. mid-stream disconnect |
| infra/pi/agent/tests/test_health.py | +25 | Health gate: 200 / 503 / refused |
| infra/pi/agent/deploy/voice-agent-worker.service | +49 | systemd unit |
| infra/pi/agent/README.md | +154 | Operator runbook |
| infra/pi/agent/.env.example | +14 | Env template |
| infra/pi/agent/requirements.txt | +11 | Pinned deps |
| infra/pi/agent/.gitignore | +6 | Ignore venv/.env/caches |

## Issues Found

| Severity | Score | Category | File:line | Description |
| -------- | ----- | -------- | --------- | ----------- |
| Important | 55 | bugs | config.py:43-60 + tts_plugin.py:26 | `tts_sample_rate` parsed/validated but never used; plugin hardcoded 24000 — dead-config trap, docs said "do not change" |
| Minor | 30 | quality | agent.py:66-67 | Redundant second `await handle.wait_for_playout()` after `await session.say()` |
| Minor | 28 | quality | agent.py:41,77 | `load_config()` runs in both `entrypoint` and `__main__` |
| Minor | 22 | tests | tests/ | No test for the fixture's `mode="disconnect"` path |
| Minor | 18 | documentation | README.md:54, .env.example:11 | "`TTS_SAMPLE_RATE` … do not change" overstated the (then-unused) knob |

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| `tts_sample_rate` unused (Important) | `ee4fbf2` | Wired `cfg.tts_sample_rate` through `DesktopTTS` → `AudioEmitter.initialize`; default stays 24000 |
| Redundant await (Minor) | `ee4fbf2` | Collapsed to a single `await session.say(...)` |
| Missing disconnect test (Minor) | `ee4fbf2` | Added `test_disconnect_midstream_raises` (asserts `websockets.ConnectionClosed`); suite now 8 passing |
| Misleading sample-rate docs (Minor) | `ee4fbf2` | README + `.env.example` now explain the value labels published frames and must match the server's 24 kHz |

## Skipped Issues

| Issue | Reason |
| ----- | ------ |
| Double `load_config()` (agent.py:41,77) — Minor | Not a true redundancy: the `entrypoint` load is per-job (picks up rotated keys); the `__main__` load supplies credentials to `WorkerOptions` (the CLI does not read `.env`). Both serve distinct purposes. |

## Recommendations

- Run the manual on-Pi e2e smoke to close the live acceptance criteria (SFU registration, greeting playout without pitch/speed artifacts, systemd restart).
- Confirm the worker's default HTTP port (prod 8081) does not collide with other Pi services in the real deployment; make it configurable if it does.

## Orchestrator note

During fixes, the fix-agent introduced two undisclosed changes to `agent.py`'s `__main__`: explicit credential passing (kept — it correctly fixes manual foreground runs, verified against the 1.6.2 API) and a hardcoded `port=9812` (removed — unjustified magic with no evidence of an 8081 collision).
