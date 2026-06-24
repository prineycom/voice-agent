# Report: 11-agent-worker-scaffold-tts-greeting

**Plan:** `.yoke/ai/11-agent-worker-scaffold-tts-greeting/11-agent-worker-scaffold-tts-greeting-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                          | Status  | Commit    | Concerns |
| --- | ----------------------------- | ------- | --------- | -------- |
| 1   | Skeleton + deps + env + gitignore | ✅ DONE | `546eba9` | — (Risk 7 cleared) |
| 2   | Config module                 | ✅ DONE | `e04ec1b` | —        |
| 3   | TTS plugin (core)             | ✅ DONE | `de2def3` | —        |
| 4   | Health gate                   | ✅ DONE | `b91f500` | —        |
| 5   | Worker entrypoint + greeting  | ✅ DONE | `76a3a39` | — (Risk 1 cleared) |
| 6   | Tests                         | ✅ DONE | `ebdd8bb` | —        |
| 7   | systemd unit                  | ✅ DONE | `ba4ab49` | —        |
| 8   | README runbook                | ✅ DONE | `79e5dc0` | —        |
| 9   | Validation                    | ✅ pass | —         | —        |

## Post-implementation

| Step          | Status      | Commit |
| ------------- | ----------- | ------ |
| Validate      | ✅ pass     | —      |
| Documentation | ⏭️ skipped (opt-in; README shipped as Task 8) | — |
| Format        | ⏭️ N/A (no formatter configured in the project) | — |

## Go/no-go spikes resolved

- **Risk 7 (ARM64 wheels):** GO. `livekit-agents==1.6.2` (+ `livekit==1.1.9`, `aiohttp==3.14.1`) installs cleanly on the Pi 5 (aarch64, Python 3.13.5) — all native deps ship `manylinux2014_aarch64` wheels, nothing built from source.
- **Risk 1 (TTS-only AgentSession):** GO. `AgentSession(tts=...)` constructs with only a TTS provider (no STT/LLM); `await session.say(text)` performs TTS-only playout. No `rtc.AudioSource` fallback needed.

## Review

- **Task 3 (TTS plugin):** reviewed by `code-reviewer` agent against the installed 1.6.2 API — ✅ Approved. Confirmed `AudioEmitter.initialize/push/flush` usage, `mime_type="audio/pcm"` (raw-PCM path, framework resamples), odd-byte leftover-carry traced correct, socket closed in `finally`, `{"error"}` → `APIError` (the type the framework's retry loop catches). Two optional Minor notes (log a dropped trailing orphan byte; redundant typed alias) — non-blocking, not actioned.
- **Tasks 2, 4, 5:** reviewed by orchestrator inspection — spec-compliant, fail-fast/error paths verified.
- **Task 6:** 7 tests pass; re-run independently by the orchestrator.

## Validation

`.venv/bin/python -m py_compile` (all 7 modules) ✅
`.venv/bin/python -c "import config, tts_plugin, health, agent"` ✅
`.venv/bin/python -m pytest tests/ -q` ✅ (7 passed, 0 failed)
Lint / type-check / build — N/A (no tooling configured; pure-Python package, remote inference).

## Acceptance criteria status

| AC | Status |
| --- | --- |
| Worker runs from a Pi venv under a systemd unit | ✅ unit + venv (live start is the manual on-Pi smoke) |
| Worker registers with the SFU and joins a room when dispatched | ⏳ code complete; **manual on-Pi smoke** |
| Custom TTS plugin streams PCM16 @24kHz over `/tts` WS | ✅ implemented + unit-tested vs fake server |
| Participant hears the Russian greeting, correctly resampled | ⏳ **manual on-Pi smoke** (Risk 2 check) |
| Startup `/health` gate yields a clear logged error, not a hang | ✅ implemented + unit-tested |
| README documents venv, systemd, dispatch/join smoke | ✅ |
| Unit tests for plugin + health gate pass in CI (no hardware) | ✅ 7 passing |

## Changes summary

| File | Action | Description |
| --- | --- | --- |
| `infra/pi/agent/requirements.txt` | created | Pinned deps (livekit-agents 1.6.2, websockets 16.0, python-dotenv 1.2.2) + dev |
| `infra/pi/agent/.gitignore` | created | Ignore `.venv/`, `.env`, `__pycache__/`, `*.wav`, `models/` |
| `infra/pi/agent/.env.example` | created | Documented env template (LiveKit + TTS + greeting) |
| `infra/pi/agent/config.py` | created | `AgentConfig` + `load_config()` with fail-fast on missing keys |
| `infra/pi/agent/tts_plugin.py` | created | `DesktopTTS` — `tts.TTS` adapter over `/tts` WS, 24kHz mono, odd-byte carry |
| `infra/pi/agent/health.py` | created | `check_tts_health()` startup gate, `TTSHealthError` |
| `infra/pi/agent/agent.py` | created | TTS-only `AgentSession` entrypoint; greeting on participant join |
| `infra/pi/agent/deploy/voice-agent-worker.service` | created | systemd unit (venv python `-m agent start`, journald, restart) |
| `infra/pi/agent/tests/conftest.py` | created | Fake `/tts` WS + `/health` HTTP servers |
| `infra/pi/agent/tests/test_tts_plugin.py` | created | Plugin behavior: framing, odd-byte, error, teardown |
| `infra/pi/agent/tests/test_health.py` | created | Health gate: 200 / 503 / refused |
| `infra/pi/agent/README.md` | created | Operator runbook: setup, config, systemd, health, smoke, troubleshooting |

## Commits

- `25e7189` #11 docs(11-agent-worker-scaffold-tts-greeting): add implementation plan
- `546eba9` #11 chore(...): scaffold agent worker package and deps
- `e04ec1b` #11 feat(...): add agent worker config module
- `b91f500` #11 feat(...): add TTS health startup gate
- `de2def3` #11 feat(...): add TTS WebSocket plugin
- `76a3a39` #11 feat(...): add worker entrypoint with TTS greeting
- `ebdd8bb` #11 test(...): add TTS plugin and health gate tests
- `ba4ab49` #11 chore(...): add systemd unit for the worker
- `79e5dc0` #11 docs(...): add agent worker README runbook

## Remaining manual step

The on-Pi e2e smoke test (start the worker against the live SFU + Desktop TTS, join a room, hear the Russian greeting, confirm no pitch/speed artifacts, restart via systemd) is documented in `infra/pi/agent/README.md` and must be run on the Pi to fully close the dispatch/join and resampling acceptance criteria.
